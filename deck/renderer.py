"""Helpers de alto nivel para pintar teclas individuales o el deck completo."""

from __future__ import annotations

from typing import Any

from config import RESERVED_KEYS
from deck.primitives import solid_tile, text_tile
from deck.style import (
    COLOR_EMPTY,
    COLOR_ERROR,
    COLOR_HABIT_DONE,
    COLOR_HABIT_PENDING,
    COLOR_RESERVED,
    COLOR_TEXT_HABIT_DONE,
    COLOR_TEXT_HABIT_PENDING,
    FONT_SIZE_HABIT_PENDING,
)
from provider.base import Habit


def render_habit(deck: Any, key: int, habit: Habit | None, done: bool, current_value: float | None = None) -> None:
    """Pinta una tecla de habito.

    Pendiente: fondo blanco y texto negro grande, para resaltar sobre el
    resto. Hecho hoy: fondo gris oscuro y texto atenuado, para pasar
    desapercibido. Si ``habit`` es ``None`` la tecla se pinta vacia (negra).
    El texto lo decide ``habit.display_label`` (nombre sin emoji para
    booleanos, solo progreso para habitos cuantificables); el emoji del icono
    (``habit.emoji``), si tiene, se pinta aparte como icono a color.

    Args:
        current_value: Progreso acumulado hoy, si se conoce (solo relevante
            para habitos cuantificables).
    """
    if habit is None:
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    label = habit.display_label(current_value)
    if done:
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


def render_checkin_error(deck: Any, key: int, code: str) -> None:
    """Pinta una tecla en rojo con un codigo de error corto."""
    deck.set_key_image(key, text_tile(deck, COLOR_ERROR, code))


def render_reserved(deck: Any, key: int) -> None:
    """Pinta una tecla reservada con su color gris apagado."""
    deck.set_key_image(key, solid_tile(deck, COLOR_RESERVED))


def render_empty(deck: Any, key: int) -> None:
    """Pinta una tecla vacia (negra)."""
    deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))


def render_all(
    deck: Any,
    mapping: dict[str, int],
    habits_by_id: dict[str, Habit],
    done_ids: set[str],
    values_by_id: dict[str, float],
) -> None:
    """Repinta las 15 teclas segun el mapeo, los habitos y los checkins de hoy.

    Args:
        deck: El dispositivo Stream Deck.
        mapping: Mapeo habito -> tecla.
        habits_by_id: Objetos ``Habit`` indexados por id.
        done_ids: Ids de habitos con checkin completado hoy.
        values_by_id: Progreso acumulado hoy por habito (habitos
            cuantificables sin checkin hoy simplemente no aparecen).
    """
    key_to_habit_id = {k: hid for hid, k in mapping.items()}
    for key in range(deck.key_count()):
        if key in RESERVED_KEYS:
            render_reserved(deck, key)
            continue
        habit_id = key_to_habit_id.get(key)
        habit = habits_by_id.get(habit_id) if habit_id else None
        done = habit.is_done_today(done_ids) if habit else False
        current_value = values_by_id.get(habit_id) if habit_id else None
        render_habit(deck, key, habit, done, current_value)


def render_error_all(deck: Any, mapping: dict[str, int], code: str) -> None:
    """Pinta con el codigo de error corto todas las teclas actualmente
    mapeadas a un habito (se usa cuando falla la lectura de habitos o de
    checkins, para no dejar informacion potencialmente obsoleta en pantalla)."""
    for key in mapping.values():
        render_checkin_error(deck, key, code)
