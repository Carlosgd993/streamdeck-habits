from config import KEY_REFRESH, RESERVED_KEYS
from deck_renderer import render_reserved


def render_reserved_keys(deck):
    """Pinta las teclas reservadas (0, 5, 10). Salvo la tecla de refresco
    (KEY_REFRESH), son placeholder: por ahora no reaccionan a pulsaciones --
    quedan libres para configuracion futura."""
    for key in RESERVED_KEYS:
        render_reserved(deck, key)


def handle_key_press(key, refresh_event):
    """Gestiona la pulsacion de una tecla reservada. Devuelve True si la
    tecla era reservada (y por tanto ya gestionada u omitida a proposito),
    False si el llamador debe tratarla como una tecla de habito normal."""
    if key == KEY_REFRESH:
        refresh_event.set()
        return True
    return key in RESERVED_KEYS
