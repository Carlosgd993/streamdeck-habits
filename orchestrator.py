#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos
del proveedor con las teclas del Stream Deck y gestiona los pasos al pulsar.

El orquestador depende solo del puerto abstracto ``provider.base`` (interfaz
``HabitProvider``, modelo ``Habit`` y excepciones ``Provider*``); la unica
linea acoplada a un backend concreto es la construccion del proveedor
(``SupabaseProvider()``). Sustituir de API = escribir otro adaptador que
implemente ``HabitProvider`` y cambiar esa linea.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
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
from provider.supabase import SupabaseProvider

state_lock = threading.Lock()
pending_requests: set[str] = set()  # habit_ids con paso en vuelo, para no duplicar por doble pulsacion


def make_key_callback(
    deck: Any,
    provider: HabitProvider,
    mapping: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    refresh_event: Event,
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para el mapeo actual.

    El closure resultante gestiona la tecla de refresco, ignora teclas sin
    habito asignado, evita pasos duplicados por habito (``pending_requests`` +
    ``state_lock``) y, al pulsar una tecla de habito, pide al proveedor que
    avance un paso y repinta la tecla al momento con el nuevo total (sin
    esperar al proximo ciclo de refresco) -- exito en blanco/gris segun si
    alcanza el objetivo, fallo en rojo con codigo.

    Una tecla con el objetivo ya alcanzado hoy se sigue pudiendo pulsar: es la
    base de datos quien decide el nuevo valor (``habit_step``), y un habito
    cuantificable sigue sumando sin tope.

    Args:
        deck: El dispositivo Stream Deck.
        provider: Proveedor de habitos (puerto abstracto).
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
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
        if habit is None:
            # habito desconocido (caso defensivo entre ciclos): se ignora la pulsacion
            with state_lock:
                pending_requests.discard(habit_id)
            return

        try:
            new_value = provider.step(habit)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc))
            try:
                renderer.render_checkin_error(deck, key, code)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Paso FALLO [{code}]: {habit_id}", flush=True)
        else:
            habit.current_value = new_value
            try:
                renderer.render_habit(deck, key, habit)
            except Exception as device_exc:
                health.log_device_error(str(device_exc))
            print(f"Paso OK: {habit.name} -> {new_value}", flush=True)

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
        provider: HabitProvider = SupabaseProvider()
    except ProviderError as exc:
        print(f"No se pudo inicializar el proveedor de habitos: {exc}", flush=True)
        sys.exit(1)

    session = DeckSession()
    session.open()

    mapping = key_map.load_map()
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
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

        renderer.render_all(deck, mapping, habits_ref["value"])
        deck.set_key_callback(make_key_callback(deck, provider, mapping, habits_ref, refresh_event))

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
