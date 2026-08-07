"""Helpers de alto nivel para pintar teclas individuales o el deck completo."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from config import KEY_MENU, KEY_PAGE_NEXT, KEY_PAGE_PREV
from core.screens import MenuEntry, NumericKey, OptionEntry, ResolvedPage, StandbyKey
from deck.primitives import solid_tile, text_tile
from deck.style import (
    COLOR_ARROW,
    COLOR_CONFIRM,
    COLOR_EMPTY,
    COLOR_ERROR,
    COLOR_HABIT_DONE,
    COLOR_HABIT_PENDING,
    COLOR_MENU,
    COLOR_NAV,
    COLOR_NEUTRAL,
    COLOR_NUMERIC,
    COLOR_NUMERIC_BACKSPACE,
    COLOR_NUMERIC_DISPLAY,
    COLOR_OPTIONS_MESSAGE,
    COLOR_SHUTDOWN,
    COLOR_STANDBY,
    COLOR_TASK_BY_PRIORITY,
    COLOR_TASK_SENDING,
    COLOR_TEMPLATE,
    COLOR_TEMPLATE_PENDING,
    COLOR_TEXT_ARROW,
    COLOR_TEXT_CONFIRM,
    COLOR_TEXT_HABIT_DONE,
    COLOR_TEXT_HABIT_PENDING,
    COLOR_TEXT_NAV,
    COLOR_TEXT_NUMERIC,
    COLOR_TEXT_NUMERIC_BACKSPACE,
    COLOR_TEXT_NUMERIC_DISPLAY,
    COLOR_TEXT_OPTIONS_MESSAGE,
    COLOR_TEXT_SHUTDOWN,
    COLOR_TEXT_STANDBY,
    COLOR_TEXT_TASK_BY_PRIORITY,
    COLOR_TEXT_TEMPLATE,
    COLOR_TEXT_TEMPLATE_PENDING,
    FONT_SIZE_HABIT_PENDING,
    FONT_SIZE_NAV,
    FONT_SIZE_NUMERIC,
    FONT_SIZE_NUMERIC_DISPLAY,
    FONT_SIZE_OPTIONS,
    FONT_SIZE_STANDBY,
    FONT_SIZE_TASK,
    FONT_SIZE_TEMPLATE,
)
from provider.base import Habit, Task, Template

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


def render_template(deck: Any, key: int, template: Template | None) -> None:
    """Pinta una tecla de plantilla de creacion rapida (vista "Crear").

    Morado si la plantilla esta lista para usarse, y gris apagado -- el mismo de
    un habito ya hecho -- si ya tiene una ocurrencia pendiente
    (``template.has_pending``). Aqui el gris **si** significa "deshabilitada", al
    reves que en un habito: pulsarla no hace nada, porque crear otra ocurrencia
    seria un duplicado silencioso (ver ``core.screens.resolve_press``).

    No hay variante "creada": una plantilla nunca desaparece de la vista, se
    reutiliza. Si ``template`` es ``None`` la tecla se pinta vacia.
    """
    if template is None:
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    if template.has_pending:
        color, text_color = COLOR_TEMPLATE_PENDING, COLOR_TEXT_TEMPLATE_PENDING
    else:
        color, text_color = COLOR_TEMPLATE, COLOR_TEXT_TEMPLATE
    image = text_tile(
        deck,
        color,
        template.display_label(),
        text_color=text_color,
        font_size=FONT_SIZE_TEMPLATE,
        emoji=template.emoji,
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


def render_numeric_key(deck: Any, key: int, nk: NumericKey) -> None:
    """Pinta un boton del teclado numerico de entrada manual (ver
    ``core.screens.NUMERIC_KEYPAD``).

    "Salir" reutiliza el azul generico de navegacion (``COLOR_NAV``): es el
    mismo rol de "esto te saca de aqui" que la tecla de menu. El resto usa una
    familia de colores propia que no choca con habito/tarea/plantilla/nav (ver
    ``deck/style.py``): digitos y "." en gris azulado, "Borrar" en ambar
    (distinto de ``COLOR_ERROR``), "OK" en verde estatico, y la "pantalla" del
    valor tecleado en un tono oscuro propio.
    """
    if nk.kind == "display":
        image = text_tile(
            deck,
            COLOR_NUMERIC_DISPLAY,
            nk.label or "0",
            text_color=COLOR_TEXT_NUMERIC_DISPLAY,
            font_size=FONT_SIZE_NUMERIC_DISPLAY,
        )
    elif nk.kind == "cancel":
        image = text_tile(deck, COLOR_NAV, nk.label, text_color=COLOR_TEXT_NAV, font_size=FONT_SIZE_NAV, emoji="✕")
    elif nk.kind == "backspace":
        image = text_tile(
            deck,
            COLOR_NUMERIC_BACKSPACE,
            nk.label,
            text_color=COLOR_TEXT_NUMERIC_BACKSPACE,
            font_size=FONT_SIZE_NAV,
            emoji="⌫",
        )
    elif nk.kind == "confirm":
        image = text_tile(
            deck, COLOR_CONFIRM, nk.label, text_color=COLOR_TEXT_CONFIRM, font_size=FONT_SIZE_NAV, emoji="✔️"
        )
    else:  # "digit" | "decimal"
        image = text_tile(deck, COLOR_NUMERIC, nk.label, text_color=COLOR_TEXT_NUMERIC, font_size=FONT_SIZE_NUMERIC)
    deck.set_key_image(key, image)


def render_standby_key(deck: Any, key: int, sk: StandbyKey) -> None:
    """Pinta una tecla con contenido de la pantalla de stand by (ver
    ``core.screens.STANDBY_LAYOUT``).

    Fondo negro como una tecla vacia: con el brillo al minimo lo unico que debe
    llegar a verse es el icono, que es lo que distingue un deck suspendido de
    uno apagado o colgado. Si la fuente de emoji no esta instalada queda la
    tecla en negro y se pierde esa senal, pero no falla nada (se degrada, igual
    que el resto de iconos)."""
    if not sk.label and not sk.emoji:
        deck.set_key_image(key, solid_tile(deck, COLOR_STANDBY))  # tecla sin contenido
        return
    image = text_tile(
        deck, COLOR_STANDBY, sk.label, text_color=COLOR_TEXT_STANDBY, font_size=FONT_SIZE_STANDBY, emoji=sk.emoji
    )
    deck.set_key_image(key, image)


def render_option_entry(deck: Any, key: int, entry: OptionEntry) -> None:
    """Pinta una tecla de la pantalla de opciones de un habito/tarea (ver
    ``core.screens.HABIT_OPTIONS_LAYOUT``/``TASK_OPTIONS_LAYOUT``), la que se abre al mantener pulsado
    un habito o una tarea.

    "Volver" reutiliza el azul generico de navegacion (``COLOR_NAV``): mismo
    rol que "Salir" en el teclado numerico, esto te saca de aqui sin tocar el
    habito/tarea que abrio el menu. Un mensaje informativo usa un color propio
    apagado, para no parecer una tecla de contenido pulsable. Una tecla de
    prioridad (solo en ``TASK_OPTIONS_LAYOUT``) reutiliza literalmente los
    colores de tarea por prioridad (``COLOR_TASK_BY_PRIORITY``), para que se
    reconozca de un vistazo con el mismo codigo de color que el resto del
    deck. Una tecla sin contenido se pinta vacia.
    """
    if entry.kind == "back":
        image = text_tile(
            deck, COLOR_NAV, entry.label, text_color=COLOR_TEXT_NAV, font_size=FONT_SIZE_NAV, emoji=entry.emoji
        )
    elif entry.kind == "message":
        image = text_tile(
            deck,
            COLOR_OPTIONS_MESSAGE,
            entry.label,
            text_color=COLOR_TEXT_OPTIONS_MESSAGE,
            font_size=FONT_SIZE_OPTIONS,
            emoji=entry.emoji,
        )
    elif entry.kind == "priority":
        color = COLOR_TASK_BY_PRIORITY.get(entry.priority, COLOR_TASK_BY_PRIORITY[_DEFAULT_PRIORITY])
        text_color = COLOR_TEXT_TASK_BY_PRIORITY.get(entry.priority, COLOR_TEXT_TASK_BY_PRIORITY[_DEFAULT_PRIORITY])
        image = text_tile(deck, color, entry.label, text_color=text_color, font_size=FONT_SIZE_TASK)
    else:  # "blank"
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    deck.set_key_image(key, image)


def render_page(deck: Any, resolved: ResolvedPage) -> None:
    """Repinta las 15 teclas segun la pagina ya resuelta por ``core.screens``.

    Cada tecla se resuelve en orden: stand by, teclado numerico y menu de
    opciones primero (cada uno cubre las 15 y van antes porque ahi 0/5/10 no
    son menu/paginacion), luego menu (fija), flecha de paginacion o neutra,
    entrada de menu/sistema, habito, tarea, plantilla y, si no es nada de eso,
    vacia.
    ``resolved`` ya trae listo que hay en cada tecla; esta funcion solo pinta.

    Args:
        deck: El dispositivo Stream Deck.
        resolved: La pagina activa, resuelta con ``core.screens.resolve_page``.
    """
    for key in range(deck.key_count()):
        if key in resolved.key_standby:
            render_standby_key(deck, key, resolved.key_standby[key])
        elif key in resolved.key_numeric:
            render_numeric_key(deck, key, resolved.key_numeric[key])
        elif key in resolved.key_options:
            render_option_entry(deck, key, resolved.key_options[key])
        elif key == KEY_MENU:
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
        elif key in resolved.key_template:
            render_template(deck, key, resolved.key_template[key])
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
