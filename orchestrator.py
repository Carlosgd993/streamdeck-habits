#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos
del proveedor con las teclas del Stream Deck y gestiona los checkins al pulsar.

El orquestador depende solo del puerto abstracto ``provider.base`` (interfaz
``HabitProvider``, modelo ``Habit``/``Progress`` y excepciones ``Provider*``);
la unica linea acoplada a un backend concreto es la construccion del proveedor
(``TickTickProvider()``). Sustituir de API = escribir otro adaptador que
implemente ``HabitProvider`` y cambiar esa linea.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from datetime import date, datetime
from threading import Event
from typing import Any

import core.health as health
import core.key_map as key_map
import deck.keys as deck_keys
import deck.renderer as renderer
from config import REFRESH_SECONDS
from core.error_codes import CODES
from deck.session import DeckSession
from provider.base import Habit, HabitProvider, ProviderError
from provider.ticktick import TickTickProvider

state_lock = threading.Lock()
pending_requests: set[str] = set()  # habit_ids con checkin en vuelo, para no duplicar por doble pulsacion


def today() -> date:
    """Devuelve el dia local actual."""
    return date.today()


def make_key_callback(
    deck: Any,
    provider: HabitProvider,
    mapping: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    done_ids_ref: dict[str, set[str]],
    values_ref: dict[str, dict[str, float]],
    refresh_event: Event,
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para el mapeo actual.

    El closure resultante gestiona la tecla de refresco, ignora teclas sin
    habito asignado, evita checkins duplicados por habito (``pending_requests``
    + ``state_lock``), ignora pulsaciones sobre habitos ya bloqueados
    (``habit.is_locked``, p.ej. un habito cuantificable que ya alcanzo su
    objetivo) y, al pulsar una tecla de habito, envia el checkin al proveedor
    pintando la tecla en verde/gris (exito) o rojo con codigo (fallo).

    Args:
        deck: El dispositivo Stream Deck.
        provider: Proveedor de habitos (puerto abstracto).
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
        done_ids_ref: Wrapper analogo con el conjunto de ids hechos hoy.
        values_ref: Wrapper analogo con el progreso acumulado hoy por habito
            (solo relevante para habitos cuantificables).
        refresh_event: Evento que despierta el bucle principal (refresco manual).

    Returns:
        El callback ``on_key_change(deck, key, pressed)`` para el Stream Deck.
    """
    key_to_habit_id = {k: hid for hid, k in mapping.items()}

    def on_key_change(deck: Any, key: int, pressed: bool) -> None:
        if not pressed:
            return
        if deck_keys.handle_key_press(key, refresh_event):
            return  # tecla reservada (p.ej. refresco manual), ya gestionada
        habit_id = key_to_habit_id.get(key)
        if habit_id is None:
            return  # sin habito asignado todavia

        with state_lock:
            if habit_id in pending_requests:
                return  # ya hay una peticion en vuelo para este habito
            pending_requests.add(habit_id)

        habit = habits_ref["value"].get(habit_id)
        if habit is None or habit.is_locked(done_ids_ref["value"]):
            # habito desconocido (caso defensivo entre ciclos) u objetivo ya
            # alcanzado: la tecla no admite mas progreso, se ignora la pulsacion
            with state_lock:
                pending_requests.discard(habit_id)
            return

        current_value = values_ref["value"].get(habit_id, 0.0)
        day = today()
        new_value = habit.next_value(current_value)

        try:
            provider.checkin(habit, day, new_value)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc))
            try:
                renderer.render_checkin_error(deck, key, code)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Checkin FALLO [{code}]: {habit_id} ({day.isoformat()})", flush=True)
        else:
            values_ref["value"][habit_id] = new_value
            done_now = new_value >= habit.goal
            if done_now:
                done_ids_ref["value"].add(habit_id)
            try:
                renderer.render_habit(deck, key, habit, done=done_now, current_value=new_value)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Checkin OK: {habit.name} ({day.isoformat()})", flush=True)

        with state_lock:
            pending_requests.discard(habit_id)

    return on_key_change


def main() -> None:
    """Arranca el daemon: construye el proveedor, abre el deck y corre el bucle.

    Cada iteracion ejecuta ``refresh_cycle`` y luego espera ``REFRESH_SECONDS``
    o hasta que se active ``refresh_event`` (refresco manual). Cualquier
    excepcion ajena al proveedor de habitos se trata como error de dispositivo
    y dispara una reconexion.
    """
    try:
        provider: HabitProvider = TickTickProvider()
    except ProviderError as exc:
        print(f"No se pudo inicializar el proveedor de habitos: {exc}", flush=True)
        sys.exit(1)

    print(f"Fecha/hora local interpretada al arrancar: {datetime.now().isoformat()}", flush=True)

    session = DeckSession()
    session.open()

    mapping = key_map.load_map()
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    done_ids_ref: dict[str, set[str]] = {"value": set()}
    values_ref: dict[str, dict[str, float]] = {"value": {}}  # progreso acumulado hoy, habitos cuantificables
    refresh_event = threading.Event()  # se activa por el timer o al pulsar KEY_REFRESH

    def refresh_cycle() -> None:
        nonlocal mapping
        deck = session.deck
        deck_keys.render_reserved_keys(deck)

        try:
            habits = provider.get_habits()
        except ProviderError as exc:
            _, code = health.classify(exc)
            print(f"[{code}] {CODES[code]}: {exc}", flush=True)
            renderer.render_error_all(deck, mapping, code)
            return

        mapping = key_map.update_mapping(habits, mapping)
        habits_ref["value"] = {h.id: h for h in habits}

        day = today()
        try:
            progress = provider.get_progress(list(mapping.keys()), day)
        except ProviderError as exc:
            _, code = health.classify(exc)
            print(f"[{code}] {CODES[code]}: {exc}", flush=True)
            renderer.render_error_all(deck, mapping, code)
            return

        done_ids = {hid for hid, p in progress.items() if p.value >= p.goal}
        values_by_id = {hid: p.value for hid, p in progress.items()}
        done_ids_ref["value"] = done_ids
        values_ref["value"] = values_by_id
        renderer.render_all(deck, mapping, habits_ref["value"], done_ids, values_by_id)
        deck.set_key_callback(
            make_key_callback(deck, provider, mapping, habits_ref, done_ids_ref, values_ref, refresh_event)
        )

    try:
        while True:
            try:
                refresh_cycle()
            except Exception as exc:
                # Cualquier fallo que no sea del proveedor de habitos (esos ya se
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
