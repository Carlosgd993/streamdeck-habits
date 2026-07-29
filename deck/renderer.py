"""Helpers de alto nivel para pintar teclas individuales o el deck completo."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from config import KEY_MENU, KEY_PAGE_NEXT, KEY_PAGE_PREV
from core.screens import MenuEntry, ResolvedPage
from deck.primitives import solid_tile, text_tile
from deck.style import (
    COLOR_ARROW,
    COLOR_EMPTY,
    COLOR_ERROR,
    COLOR_HABIT_DONE,
    COLOR_HABIT_PENDING,
    COLOR_MENU,
    COLOR_NAV,
    COLOR_NEUTRAL,
    COLOR_SHUTDOWN,
    COLOR_TASK_BY_PRIORITY,
    COLOR_TASK_SENDING,
    COLOR_TEXT_ARROW,
    COLOR_TEXT_HABIT_DONE,
    COLOR_TEXT_HABIT_PENDING,
    COLOR_TEXT_NAV,
    COLOR_TEXT_SHUTDOWN,
    COLOR_TEXT_TASK_BY_PRIORITY,
    FONT_SIZE_HABIT_PENDING,
    FONT_SIZE_NAV,
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


def render_empty(deck: Any, key: int) -> None:
    """Pinta una tecla vacia (negra)."""
    deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))


def render_menu_key(deck: Any, key: int) -> None:
    """Pinta la tecla de menu (``config.KEY_MENU``, fija en toda pantalla)."""
    deck.set_key_image(key, text_tile(deck, COLOR_MENU, "Menu", text_color=COLOR_TEXT_NAV, emoji="🧭"))


def render_arrow(deck: Any, key: int, direction: Literal["prev", "next"], *, enabled: bool) -> None:
    """Pinta la flecha de paginacion (5 = anterior, 10 = siguiente).

    El icono se pinta siempre, activa o no la paginacion, para que la tecla
    se reconozca de un vistazo igual que la de menu; lo que distingue si
    hace algo es el fondo: ``COLOR_ARROW`` cuando hay mas de una pagina,
    ``COLOR_NEUTRAL`` (el mismo gris apagado de antes) cuando no.
    """
    color = COLOR_ARROW if enabled else COLOR_NEUTRAL
    emoji = "◀️" if direction == "prev" else "▶️"
    deck.set_key_image(key, text_tile(deck, color, "", text_color=COLOR_TEXT_ARROW, emoji=emoji))


def render_nav_entry(deck: Any, key: int, entry: MenuEntry) -> None:
    """Pinta un boton de menu o de submenu Sistema.

    El boton "Apagar" usa el rojo de aviso (``COLOR_SHUTDOWN``) para seguir
    distinguiendose como accion destructiva, igual que antes cuando era una
    tecla reservada fija; el resto usa el azul generico de navegacion."""
    if entry.action == "shutdown":
        color, text_color = COLOR_SHUTDOWN, COLOR_TEXT_SHUTDOWN
    else:
        color, text_color = COLOR_NAV, COLOR_TEXT_NAV
    image = text_tile(deck, color, entry.label, text_color=text_color, font_size=FONT_SIZE_NAV, emoji=entry.emoji)
    deck.set_key_image(key, image)


def render_page(deck: Any, resolved: ResolvedPage) -> None:
    """Repinta las 15 teclas segun la pagina ya resuelta por ``core.screens``.

    Cada tecla se resuelve en orden: menu (fija), flecha de paginacion o
    neutra, entrada de menu/sistema, habito, tarea y, si no es nada de eso,
    vacia. ``resolved`` ya trae listo que hay en cada tecla; esta funcion solo
    pinta.

    Args:
        deck: El dispositivo Stream Deck.
        resolved: La pagina activa, resuelta con ``core.screens.resolve_page``.
    """
    for key in range(deck.key_count()):
        if key == KEY_MENU:
            render_menu_key(deck, key)
        elif key in (KEY_PAGE_PREV, KEY_PAGE_NEXT):
            direction = "prev" if key == KEY_PAGE_PREV else "next"
            render_arrow(deck, key, direction, enabled=resolved.total_pages > 1)
        elif key in resolved.key_nav:
            render_nav_entry(deck, key, resolved.key_nav[key])
        elif key in resolved.key_habit:
            render_habit(deck, key, resolved.key_habit[key])
        elif key in resolved.key_task:
            render_task(deck, key, resolved.key_task[key])
        else:
            render_empty(deck, key)


def render_error_all(deck: Any, keys: Iterable[int], code: str) -> None:
    """Pinta con el codigo de error corto todas las ``keys`` dadas (se usa
    cuando falla una lectura, para no dejar informacion potencialmente
    obsoleta en pantalla). Se le pasan las teclas de la pagina de habitos o de
    tareas segun cual haya fallado: un fallo leyendo tareas no debe borrar los
    habitos de la pantalla, ni al reves."""
    for key in keys:
        render_checkin_error(deck, key, code)
