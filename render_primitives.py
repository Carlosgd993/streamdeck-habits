"""Helpers Pillow de bajo nivel para generar imagenes de tecla del Stream Deck."""

from __future__ import annotations

import textwrap
from typing import Any

from PIL import ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper


def solid_tile(deck: Any, color: tuple[int, int, int]) -> bytes:
    """Genera una imagen de tecla de color plano.

    Args:
        deck: El dispositivo Stream Deck (define el tamano de la imagen).
        color: Color RGB de fondo.

    Returns:
        La imagen en el formato nativo que espera ``set_key_image``.
    """
    image = PILHelper.create_image(deck, background=color)
    return PILHelper.to_native_format(deck, image)


def text_tile(deck: Any, color: tuple[int, int, int], text: str) -> bytes:
    """Genera una imagen de tecla con texto blanco centrado sobre un color.

    El texto se envuelve a un ancho fijo y se centra vertical y
    horizontalmente en la tecla.

    Args:
        deck: El dispositivo Stream Deck (define el tamano de la imagen).
        color: Color RGB de fondo.
        text: Texto a mostrar; se envuelve automaticamente si es largo.

    Returns:
        La imagen en el formato nativo que espera ``set_key_image``.
    """
    image = PILHelper.create_image(deck, background=color)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    lines = textwrap.wrap(text, width=10)
    w, h = image.size
    line_h = 12
    total_h = len(lines) * line_h
    y = (h - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), line, font=font, fill=(255, 255, 255))
        y += line_h
    return PILHelper.to_native_format(deck, image)
