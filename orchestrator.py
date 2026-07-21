#!/opt/streamdeck-habits/venv/bin/python
import sys
import threading
import time
from datetime import datetime

import auth
import deck_renderer
import habit_key_map
import health
import special_keys
from config import ENV_FILE, FAIL_LOG, REFRESH_SECONDS
from deck_session import DeckSession
from error_codes import CODES
from habits.registry import build_habit
from ticktick_client import TickTickClient, TickTickError

state_lock = threading.Lock()
pending_requests = set()  # habit_ids con checkin en vuelo, para no duplicar por doble pulsacion


def today_stamp():
    now = datetime.now()
    return int(now.strftime("%Y%m%d"))


def make_key_callback(deck, client, mapping, habits_ref, done_ids_ref):
    key_to_habit_id = {k: hid for hid, k in mapping.items()}

    def on_key_change(deck, key, pressed):
        if not pressed:
            return
        habit_id = key_to_habit_id.get(key)
        if habit_id is None:
            return  # tecla reservada o sin habito asignado todavia

        with state_lock:
            if habit_id in pending_requests:
                return  # ya hay una peticion en vuelo para este habito
            pending_requests.add(habit_id)

        habit = habits_ref["value"].get(habit_id)
        stamp = today_stamp()
        payload = habit.build_checkin_payload(stamp) if habit else {"stamp": stamp, "value": 1.0, "goal": 1.0}

        try:
            client.create_checkin(habit_id, payload)
        except TickTickError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc))
            try:
                deck_renderer.render_checkin_error(deck, key, code)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Checkin FALLO [{code}]: {habit_id} ({stamp}) - ver {FAIL_LOG}", flush=True)
        else:
            done_ids_ref["value"].add(habit_id)
            try:
                deck_renderer.render_habit(deck, key, habit, done=True)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Checkin OK: {habit.name if habit else habit_id} ({stamp})", flush=True)

        with state_lock:
            pending_requests.discard(habit_id)

    return on_key_change


def main():
    token = auth.get_token()
    if not token:
        print(f"Falta TICKTICK_ACCESS_TOKEN en {ENV_FILE}", flush=True)
        sys.exit(1)

    print(f"Fecha/hora local interpretada al arrancar: {datetime.now().isoformat()}", flush=True)

    client = TickTickClient(token)
    session = DeckSession()
    session.open()

    mapping = habit_key_map.load_map()
    habits_ref = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    done_ids_ref = {"value": set()}

    def refresh_cycle():
        nonlocal mapping
        deck = session.deck
        special_keys.render_reserved_keys(deck)

        try:
            raw_habits = client.get_habits()
        except TickTickError as exc:
            _, code = health.classify(exc)
            print(f"[{code}] {CODES[code]}: {exc}", flush=True)
            deck_renderer.render_error_all(deck, mapping, code)
            return

        mapping = habit_key_map.update_mapping(raw_habits, mapping)
        habits_ref["value"] = {h["id"]: build_habit(h) for h in raw_habits}

        stamp = today_stamp()
        try:
            done_ids = client.get_checkins_for(list(mapping.keys()), stamp)
        except TickTickError as exc:
            _, code = health.classify(exc)
            print(f"[{code}] {CODES[code]}: {exc}", flush=True)
            deck_renderer.render_error_all(deck, mapping, code)
            return

        done_ids_ref["value"] = done_ids
        deck_renderer.render_all(deck, mapping, habits_ref["value"], done_ids)
        deck.set_key_callback(make_key_callback(deck, client, mapping, habits_ref, done_ids_ref))

    try:
        while True:
            try:
                refresh_cycle()
            except Exception as exc:
                # Cualquier fallo que no sea de la API de TickTick (esos ya se
                # gestionan dentro de refresh_cycle) se trata como error de
                # dispositivo: nunca se muestra en tecla, solo a fichero.
                health.log_device_error(str(exc))
                print(f"Error de dispositivo, intentando reconectar: {exc}", flush=True)
                session.reconnect()
            time.sleep(REFRESH_SECONDS)
    finally:
        session.close()


if __name__ == "__main__":
    main()
