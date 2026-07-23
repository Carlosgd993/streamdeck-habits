"""Gestion de las teclas reservadas (0, 5, 10), incluida la de refresco manual."""

from __future__ import annotations

from threading import Event
from typing import Any

from config import KEY_REFRESH, RESERVED_KEYS
from deck.renderer import render_reserved


def render_reserved_keys(deck: Any) -> None:
    """Pinta las teclas reservadas (0, 5, 10). Salvo la tecla de refresco
    (KEY_REFRESH), son placeholder: por ahora no reaccionan a pulsaciones --
    quedan libres para configuracion futura."""
    for key in RESERVED_KEYS:
        render_reserved(deck, key)


def handle_key_press(key: int, refresh_event: Event) -> bool:
    """Gestiona la pulsacion de una tecla reservada. Devuelve True si la
    tecla era reservada (y por tanto ya gestionada u omitida a proposito),
    False si el llamador debe tratarla como una tecla de habito normal."""
    if key == KEY_REFRESH:
        refresh_event.set()
        return True
    return key in RESERVED_KEYS
