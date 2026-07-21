from config import (
    COLOR_EMPTY,
    COLOR_ERROR,
    COLOR_HABIT_DONE,
    COLOR_HABIT_PENDING,
    COLOR_RESERVED,
    RESERVED_KEYS,
)
from render_primitives import solid_tile, text_tile


def render_habit(deck, key, habit, done):
    if habit is None:
        deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))
        return
    color = COLOR_HABIT_DONE if done else COLOR_HABIT_PENDING
    deck.set_key_image(key, text_tile(deck, color, habit.name))


def render_checkin_error(deck, key, code):
    deck.set_key_image(key, text_tile(deck, COLOR_ERROR, code))


def render_reserved(deck, key):
    deck.set_key_image(key, solid_tile(deck, COLOR_RESERVED))


def render_empty(deck, key):
    deck.set_key_image(key, solid_tile(deck, COLOR_EMPTY))


def render_all(deck, mapping, habits_by_id, done_ids):
    key_to_habit_id = {k: hid for hid, k in mapping.items()}
    for key in range(deck.key_count()):
        if key in RESERVED_KEYS:
            render_reserved(deck, key)
            continue
        habit_id = key_to_habit_id.get(key)
        habit = habits_by_id.get(habit_id) if habit_id else None
        done = habit.is_done_today(done_ids) if habit else False
        render_habit(deck, key, habit, done)


def render_error_all(deck, mapping, code):
    """Pinta con el codigo de error corto todas las teclas actualmente
    mapeadas a un habito (se usa cuando falla la lectura de habitos o de
    checkins, para no dejar informacion potencialmente obsoleta en pantalla)."""
    for key in mapping.values():
        render_checkin_error(deck, key, code)
