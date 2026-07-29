"""Helpers de alto nivel para pintar teclas individuales o el deck completo."""

from __future__ import annotations

from typing import Any

from config import KEY_SHUTDOWN, RESERVED_KEYS
from deck.primitives import solid_tile, text_tile
from deck.style import (
    COLOR_EMPTY,
    COLOR_ERROR,
    COLOR_HABIT_DONE,
    COLOR_HABIT_PENDING,
    COLOR_RESERVED,
    COLOR_SHUTDOWN,
    COLOR_TASK_BY_PRIORITY,
    COLOR_TASK_SENDING,
    COLOR_TEXT_HABIT_DONE,
    COLOR_TEXT_HABIT_PENDING,
    COLOR_TEXT_SHUTDOWN,
    COLOR_TEXT_TASK_BY_PRIORITY,
    FONT_SIZE_HABIT_PENDING,
    FONT_SIZE_TASK,
)
from provider.base import Habit, Task

_DEFAULT_PRIORITY = 0  # al que cae una prioridad que no este en los diccionarios de estilo


def render_habit(deck: Any, key: int, habit: Habit | None) -> None:
    """Pinta una tecla de habito.

    Objetivo no alcanzado: fondo blanco y texto negro grande, para resaltar
    sobre el resto. Objetivo alcanzado hoy (``habit.is_done``): fondo gris
    oscuro y texto atenuado, para pasar desapercibido -- es una senal de
    "ya llegaste", no de "deshabilitado": la tecla se sigue pudiendo pulsar y
    un habito cuantificable sigue sumando por encima del objetivo (se veria
    p.ej. "10/8 Cups" en gris). Si ``habit`` es ``None`` la tecla se pinta
    vacia (negra).

    El texto lo decide ``habit.display_label()`` (nombre para booleanos, solo
    progreso para habitos cuantificables); el emoji del icono (``habit.emoji``),
    si tiene, se pinta aparte como icono a color.
    """
    if habit is None:
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    label = habit.display_label()
    if habit.is_done:
        image = text_tile(deck, COLOR_HABIT_DONE, label, text_color=COLOR_TEXT_HABIT_DONE, emoji=habit.emoji)
    else:
        image = text_tile(
            deck,
            COLOR_HABIT_PENDING,
            label,
            text_color=COLOR_TEXT_HABIT_PENDING,
            font_size=FONT_SIZE_HABIT_PENDING,
            emoji=habit.emoji,
        )
    deck.set_key_image(key, image)


def render_task(deck: Any, key: int, task: Task | None) -> None:
    """Pinta una tecla de tarea pendiente.

    El color de fondo lo da la prioridad de la tarea (blanca, verde, amarilla o
    roja, ver ``deck/style.py``); una prioridad desconocida cae al color de la
    prioridad 0 en vez de fallar. No hay variante "hecha": una tarea completada
    desaparece de la lista, y la tecla se pinta vacia (``task`` a ``None``).

    El texto es ``task.display_label()`` (el titulo, ya recortado si era largo)
    y el emoji que llevara el titulo, si lo habia, se pinta aparte como icono a
    color -- igual que en un habito.
    """
    if task is None:
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    color = COLOR_TASK_BY_PRIORITY.get(task.priority, COLOR_TASK_BY_PRIORITY[_DEFAULT_PRIORITY])
    text_color = COLOR_TEXT_TASK_BY_PRIORITY.get(task.priority, COLOR_TEXT_TASK_BY_PRIORITY[_DEFAULT_PRIORITY])
    image = text_tile(
        deck, color, task.display_label(), text_color=text_color, font_size=FONT_SIZE_TASK, emoji=task.emoji
    )
    deck.set_key_image(key, image)


def render_task_sending(deck: Any, key: int) -> None:
    """Pinta el acuse de recibo de una pulsacion de tarea: verde vivo y un check.

    Se pinta al pulsar, antes de llamar al proveedor, y se sustituye por la
    tecla vacia en cuanto la base confirma que la tarea quedo cerrada. Si la
    fuente de emoji no esta instalada queda solo el verde plano, que ya sirve
    de confirmacion (se degrada, no falla)."""
    deck.set_key_image(key, text_tile(deck, COLOR_TASK_SENDING, "", emoji="✔️"))


def render_checkin_error(deck: Any, key: int, code: str) -> None:
    """Pinta una tecla en rojo con un codigo de error corto."""
    deck.set_key_image(key, text_tile(deck, COLOR_ERROR, code))


def render_reserved(deck: Any, key: int) -> None:
    """Pinta una tecla reservada con su color gris apagado."""
    deck.set_key_image(key, solid_tile(deck, COLOR_RESERVED))


def render_shutdown(deck: Any, key: int) -> None:
    """Pinta la tecla de apagado: fondo rojo de aviso, texto y un icono
    representativo, para distinguirla a simple vista del resto de teclas
    reservadas (accion destructiva, no un simple placeholder)."""
    deck.set_key_image(key, text_tile(deck, COLOR_SHUTDOWN, "APAGAR", text_color=COLOR_TEXT_SHUTDOWN, emoji="🔴"))


def render_empty(deck: Any, key: int) -> None:
    """Pinta una tecla vacia (negra)."""
    deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))


def render_reserved_key(deck: Any, key: int) -> None:
    """Pinta una tecla reservada segun cual sea: ``KEY_SHUTDOWN`` con su aviso
    propio (``render_shutdown``), el resto con el gris generico
    (``render_reserved``). Punto unico usado tanto por el pintado inicial de
    reservadas como por ``render_all``, para que no diverjan entre si."""
    if key == KEY_SHUTDOWN:
        render_shutdown(deck, key)
    else:
        render_reserved(deck, key)


def render_all(
    deck: Any,
    mapping: dict[str, int],
    habits_by_id: dict[str, Habit],
    task_keys: dict[str, int] | None = None,
    tasks_by_id: dict[str, Task] | None = None,
) -> None:
    """Repinta las 15 teclas segun los mapeos y los datos actuales.

    Cada tecla se resuelve en orden: reservada, habito, tarea y, si no es
    ninguna de las tres, vacia. Los dos mapeos nunca se solapan (las tareas solo
    reciben teclas que los habitos dejaron libres), asi que el orden no oculta
    nada: solo fija quien manda si alguna vez lo hicieran.

    Args:
        deck: El dispositivo Stream Deck.
        mapping: Mapeo habito -> tecla.
        habits_by_id: Objetos ``Habit`` indexados por id, con su progreso de
            hoy ya incluido (``habit.current_value``).
        task_keys: Mapeo tarea -> tecla de este ciclo (volatil, no persistido).
        tasks_by_id: Objetos ``Task`` pendientes indexados por id.
    """
    key_to_habit_id = {k: hid for hid, k in mapping.items()}
    key_to_task_id = {k: tid for tid, k in (task_keys or {}).items()}
    tasks_by_id = tasks_by_id or {}
    for key in range(deck.key_count()):
        if key in RESERVED_KEYS:
            render_reserved_key(deck, key)
            continue
        habit_id = key_to_habit_id.get(key)
        if habit_id is not None:
            render_habit(deck, key, habits_by_id.get(habit_id))
            continue
        task_id = key_to_task_id.get(key)
        render_task(deck, key, tasks_by_id.get(task_id) if task_id else None)


def render_error_all(deck: Any, mapping: dict[str, int], code: str) -> None:
    """Pinta con el codigo de error corto todas las teclas de un mapeo (se usa
    cuando falla una lectura, para no dejar informacion potencialmente obsoleta
    en pantalla). Se le pasa el mapeo de habitos o el de tareas segun cual haya
    fallado: un fallo leyendo tareas no debe borrar los habitos de la pantalla,
    ni al reves."""
    for key in mapping.values():
        render_checkin_error(deck, key, code)
