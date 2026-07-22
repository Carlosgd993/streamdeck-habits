#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos
de TickTick con las teclas del Stream Deck y gestiona los checkins al pulsar."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from datetime import datetime
from threading import Event
from typing import Any

import auth
import deck_renderer
import habit_key_map
import health
import special_keys
from config import ENV_FILE, FAIL_LOG, REFRESH_SECONDS
from deck_session import DeckSession
from error_codes import CODES
from habits.base import Habit
from habits.registry import build_habit
from ticktick_client import TickTickClient, TickTickError

state_lock = threading.Lock()
pending_requests: set[str] = set()  # habit_ids con checkin en vuelo, para no duplicar por doble pulsacion


def today_stamp() -> int:
    """Devuelve el dia local actual en formato entero ``YYYYMMDD``."""
    now = datetime.now()
    return int(now.strftime("%Y%m%d"))


def make_key_callback(
    deck: Any,
    client: TickTickClient,
    mapping: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    done_ids_ref: dict[str, set[str]],
    refresh_event: Event,
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para el mapeo actual.

    El closure resultante gestiona la tecla de refresco, ignora teclas sin
    habito asignado, evita checkins duplicados por habito (``pending_requests``
    + ``state_lock``) y, al pulsar una tecla de habito, envia el checkin a
    TickTick pintando la tecla en verde (exito) o rojo con codigo (fallo).

    Args:
        deck: El dispositivo Stream Deck.
        client: Cliente de la API de TickTick.
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
        done_ids_ref: Wrapper analogo con el conjunto de ids hechos hoy.
        refresh_event: Evento que despierta el bucle principal (refresco manual).

    Returns:
        El callback ``on_key_change(deck, key, pressed)`` para el Stream Deck.
    """
    key_to_habit_id = {k: hid for hid, k in mapping.items()}

    def on_key_change(deck: Any, key: int, pressed: bool) -> None:
        if not pressed:
            return
        if special_keys.handle_key_press(key, refresh_event):
            return  # tecla reservada (p.ej. refresco manual), ya gestionada
        habit_id = key_to_habit_id.get(key)
        if habit_id is None:
            return  # sin habito asignado todavia

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


def main() -> None:
    """Arranca el daemon: valida token, abre el deck y corre el bucle de refresco.

    Cada iteracion ejecuta ``refresh_cycle`` y luego espera
    ``REFRESH_SECONDS`` o hasta que se active ``refresh_event`` (refresco
    manual). Cualquier excepcion ajena a la API de TickTick se trata como
    error de dispositivo y dispara una reconexion.
    """
    token = auth.get_token()
    if not token:
        print(f"Falta TICKTICK_ACCESS_TOKEN en {ENV_FILE}", flush=True)
        sys.exit(1)

    print(f"Fecha/hora local interpretada al arrancar: {datetime.now().isoformat()}", flush=True)

    client = TickTickClient(token)
    session = DeckSession()
    session.open()

    mapping = habit_key_map.load_map()
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    done_ids_ref: dict[str, set[str]] = {"value": set()}
    refresh_event = threading.Event()  # se activa por el timer o al pulsar KEY_REFRESH

    def refresh_cycle() -> None:
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
        deck.set_key_callback(make_key_callback(deck, client, mapping, habits_ref, done_ids_ref, refresh_event))

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
            refresh_event.wait(timeout=REFRESH_SECONDS)
            refresh_event.clear()
    finally:
        session.close()


if __name__ == "__main__":
    main()
