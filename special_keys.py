from config import RESERVED_KEYS
from deck_renderer import render_reserved


def render_reserved_keys(deck):
    """Pinta las teclas reservadas (0, 5, 10). Placeholder: por ahora no
    reaccionan a pulsaciones -- quedan libres para configuracion futura."""
    for key in RESERVED_KEYS:
        render_reserved(deck, key)
