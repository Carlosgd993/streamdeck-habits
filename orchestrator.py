#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos y
las tareas pendientes del proveedor con las teclas del Stream Deck, y gestiona
los pasos y los cierres al pulsar.

El orquestador depende solo de los puertos abstractos de ``provider.base``
(interfaces ``HabitProvider``/``TaskProvider``, modelos ``Habit``/``Task`` y
excepciones ``Provider*``); la unica linea acoplada a un backend concreto es la
construccion del proveedor (``SupabaseProvider()``). Sustituir de API = escribir
otro adaptador que implemente esos puertos y cambiar esa linea.
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
from provider.base import Habit, HabitProvider, ProviderError, Task, TaskProvider
from provider.supabase import SupabaseProvider

state_lock = threading.Lock()
pending_requests: set[str] = set()  # ids (habito o tarea) con peticion en vuelo, para no duplicar por doble pulsacion


def _claim(item_id: str) -> bool:
    """Reserva ``item_id`` si no tenia ya una peticion en vuelo.

    Returns:
        ``True`` si la pulsacion debe procesarse, ``False`` si hay que
        descartarla por duplicada.
    """
    with state_lock:
        if item_id in pending_requests:
            return False
        pending_requests.add(item_id)
        return True


def _release(item_id: str) -> None:
    """Libera la reserva de ``item_id`` hecha por ``_claim``."""
    with state_lock:
        pending_requests.discard(item_id)


def _safe_render(render: Callable[[], None]) -> None:
    """Ejecuta un repintado tratando cualquier fallo como error de dispositivo.

    Un deck que no responde nunca debe tumbar el hilo de callbacks ni pintarse
    a si mismo en tecla: solo se registra a fichero.
    """
    try:
        render()
    except Exception as device_exc:
        health.log_device_error(str(device_exc))


def make_key_callback(
    deck: Any,
    provider: HabitProvider,
    task_provider: TaskProvider,
    mapping: dict[str, int],
    task_keys: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    tasks_ref: dict[str, dict[str, Task]],
    refresh_event: Event,
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para los mapeos actuales.

    El closure resultante gestiona la tecla de refresco, ignora teclas sin nada
    asignado, evita peticiones duplicadas (``pending_requests`` + ``state_lock``)
    y actua segun lo que haya en la tecla:

    - **Habito**: pide al proveedor que avance un paso y repinta la tecla al
      momento con el nuevo total (sin esperar al proximo ciclo) -- exito en
      blanco/gris segun si alcanza el objetivo, fallo en rojo con codigo. Una
      tecla con el objetivo ya alcanzado hoy se sigue pudiendo pulsar: es la
      base quien decide el nuevo valor (``habit_step``), y un habito
      cuantificable sigue sumando sin tope.
    - **Tarea**: la pinta en verde de acuse de recibo, pide cerrarla y, solo
      cuando la base lo confirma, apaga la tecla y saca la tarea de
      ``tasks_ref`` (una segunda pulsacion ya no encuentra nada). Las demas
      teclas **no se mueven**: las tareas restantes se recolocan en el
      siguiente ciclo, no bajo el dedo.

    Args:
        deck: El dispositivo Stream Deck.
        provider: Proveedor de habitos (puerto abstracto).
        task_provider: Proveedor de tareas (puerto abstracto).
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        task_keys: Mapeo tarea -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
        tasks_ref: Idem para las tareas pendientes.
        refresh_event: Evento que despierta el bucle principal (refresco manual).

    Returns:
        El callback ``on_key_change(deck, key, pressed)`` para el Stream Deck.
    """
    key_to_habit_id = {k: hid for hid, k in mapping.items()}
    key_to_task_id = {k: tid for tid, k in task_keys.items()}

    def press_habit(deck: Any, key: int, habit_id: str) -> None:
        habit = habits_ref["value"].get(habit_id)
        if habit is None:
            return  # habito desconocido (caso defensivo entre ciclos): se ignora
        try:
            new_value = provider.step(habit)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc), kind="habit")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Paso FALLO [{code}]: {habit_id}", flush=True)
        else:
            habit.current_value = new_value
            _safe_render(lambda: renderer.render_habit(deck, key, habit))
            print(f"Paso OK: {habit.name} -> {new_value}", flush=True)

    def press_task(deck: Any, key: int, task_id: str) -> None:
        task = tasks_ref["value"].get(task_id)
        if task is None:
            return  # ya cerrada o desaparecida entre ciclos: se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            task_provider.complete_task(task)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="task")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Cierre FALLO [{code}]: {task_id}", flush=True)
        else:
            # Confirmado por la base: la tarea deja de existir para el deck. Se
            # quita de tasks_ref para que otra pulsacion no reintente cerrarla.
            tasks_ref["value"].pop(task_id, None)
            _safe_render(lambda: renderer.render_empty(deck, key))
            print(f"Tarea completada: {task.title}", flush=True)

    def on_key_change(deck: Any, key: int, pressed: bool) -> None:
        if not pressed:
            return
        if deck_keys.handle_key_press(key, refresh_event):
            return  # tecla reservada (p.ej. refresco manual), ya gestionada

        habit_id = key_to_habit_id.get(key)
        task_id = key_to_task_id.get(key)
        item_id = habit_id if habit_id is not None else task_id
        if item_id is None:
            return  # tecla sin habito ni tarea asignados

        if not _claim(item_id):
            return  # ya hay una peticion en vuelo para este elemento
        try:
            if habit_id is not None:
                press_habit(deck, key, habit_id)
            else:
                press_task(deck, key, item_id)
        finally:
            _release(item_id)

    return on_key_change


def main() -> None:
    """Arranca el daemon: construye el proveedor, abre el deck y corre el bucle.

    Cada iteracion ejecuta ``refresh_cycle`` y luego espera ``REFRESH_SECONDS``
    o hasta que se active ``refresh_event`` (refresco manual). Cualquier
    excepcion ajena a los proveedores de datos se trata como error de
    dispositivo y dispara una reconexion.
    """
    try:
        provider = SupabaseProvider()  # unica linea acoplada al backend concreto
    except ProviderError as exc:
        print(f"No se pudo inicializar el proveedor de datos: {exc}", flush=True)
        sys.exit(1)

    habit_provider: HabitProvider = provider
    task_provider: TaskProvider = provider

    session = DeckSession()
    session.open()

    mapping = key_map.load_map()
    task_keys: dict[str, int] = {}  # task_id -> tecla; volatil, se recalcula cada ciclo
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    tasks_ref: dict[str, dict[str, Task]] = {"value": {}}  # task_id -> objeto Task, actualizado cada ciclo
    refresh_event = threading.Event()  # se activa por el timer o al pulsar KEY_REFRESH

    def refresh_cycle() -> None:
        nonlocal mapping, task_keys
        deck = session.deck
        deck_keys.render_reserved_keys(deck)

        # Habitos y tareas se leen por separado y fallan por separado: un fallo
        # leyendo tareas acaba pintando su codigo solo en las teclas de tarea y
        # deja los habitos intactos, y al reves. La lectura que falla no toca su
        # mapeo ni sus datos (se conservan los del ciclo anterior), y desde
        # luego no toca los de la otra.
        habits_code: str | None = None
        try:
            habits = habit_provider.get_habits()
        except ProviderError as exc:
            _, habits_code = health.classify(exc)
            print(f"[{habits_code}] {CODES[habits_code]} (habitos): {exc}", flush=True)
        else:
            mapping = key_map.update_mapping(habits, mapping)
            habits_ref["value"] = {h.id: h for h in habits}

        tasks_code: str | None = None
        try:
            tasks = task_provider.get_tasks()
        except ProviderError as exc:
            _, tasks_code = health.classify(exc)
            print(f"[{tasks_code}] {CODES[tasks_code]} (tareas): {exc}", flush=True)
        else:
            # Las teclas de tarea salen de las que los habitos dejan libres, asi
            # que se reparten con el mapeo de habitos ya reconciliado.
            task_keys = key_map.assign_task_keys(tasks, mapping)
            tasks_ref["value"] = {t.id: t for t in tasks}
            if tasks:
                # A diferencia de los habitos, las teclas de tarea se rehacen
                # cada ciclo: sin esta linea no queda rastro de que mostraba el
                # deck en un momento dado.
                print(f"{len(tasks)} tarea(s) pendientes -> teclas {sorted(task_keys.values())}", flush=True)

        renderer.render_all(deck, mapping, habits_ref["value"], task_keys, tasks_ref["value"])
        # Los codigos de error se pintan DESPUES del repintado general, para que
        # tapen los datos viejos de la parte que fallo en vez de que el
        # repintado los borre a ellos.
        if habits_code is not None:
            renderer.render_error_all(deck, mapping, habits_code)
        if tasks_code is not None:
            renderer.render_error_all(deck, task_keys, tasks_code)
        deck.set_key_callback(
            make_key_callback(
                deck,
                habit_provider,
                task_provider,
                mapping,
                task_keys,
                habits_ref,
                tasks_ref,
                refresh_event,
            )
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
