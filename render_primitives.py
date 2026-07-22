"""Helpers Pillow de bajo nivel para generar imagenes de tecla del Stream Deck."""

from __future__ import annotations

import textwrap
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper

# Fuente de emoji a color (paquete Debian/RPi OS `fonts-noto-color-emoji`).
# Es una fuente de mapa de bits (CBDT/CBLC) con un unico tamano de glifo
# "grabado" (strike): Pillow exige pedir exactamente ese tamano al cargarla,
# por eso se prueban los valores conocidos de las versiones recientes del paquete.
_EMOJI_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
_EMOJI_FONT_CANDIDATE_SIZES = (109, 136, 128)


@lru_cache(maxsize=1)
def _emoji_font() -> ImageFont.FreeTypeFont | None:
    """Carga la fuente de emoji a color, o ``None`` si no esta instalada.

    Si el fichero no existe (fuente no instalada en el sistema) se degrada
    a no pintar emoji en vez de fallar: el resto de la tecla (texto/color)
    sigue funcionando igual.
    """
    for size in _EMOJI_FONT_CANDIDATE_SIZES:
        try:
            return ImageFont.truetype(_EMOJI_FONT_PATH, size)
        except OSError:
            continue
    return None


def _emoji_glyph(emoji: str, size: int) -> Image.Image | None:
    """Renderiza un emoji a color en una imagen cuadrada ``size x size``.

    Returns:
        La imagen RGBA con el glifo, o ``None`` si no hay fuente de emoji
        disponible o el emoji no tiene glifo en esa fuente.
    """
    font = _emoji_font()
    if font is None:
        return None
    strike_size = font.size
    glyph = Image.new("RGBA", (strike_size, strike_size), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text((0, 0), emoji, font=font, embedded_color=True)
    if glyph.getbbox() is None:
        return None  # la fuente no tiene glifo para este emoji (recuadro vacio)
    return glyph.resize((size, size), Image.LANCZOS)


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


def text_tile(
    deck: Any,
    color: tuple[int, int, int],
    text: str,
    text_color: tuple[int, int, int] = (255, 255, 255),
    font_size: int | None = None,
    emoji: str = "",
) -> bytes:
    """Genera una imagen de tecla con texto centrado sobre un color.

    El texto se envuelve a un ancho fijo y se centra vertical y
    horizontalmente en la tecla. Si se pasa ``emoji`` se pinta como icono a
    color en la mitad superior (si la fuente de emoji esta instalada; si no,
    se omite en silencio) y el texto se centra en el espacio restante.

    Args:
        deck: El dispositivo Stream Deck (define el tamano de la imagen).
        color: Color RGB de fondo.
        text: Texto a mostrar; se envuelve automaticamente si es largo.
            Cadena vacia para no mostrar texto (solo el emoji, si lo hay).
        text_color: Color RGB del texto.
        font_size: Tamano de fuente; si es ``None`` usa la fuente por
            defecto de Pillow (pequena, para textos secundarios).
        emoji: Emoji a pintar como icono, o cadena vacia para no pintar ninguno.

    Returns:
        La imagen en el formato nativo que espera ``set_key_image``.
    """
    image = PILHelper.create_image(deck, background=color)
    w, h = image.size

    emoji_h = 0
    if emoji:
        icon_size = min(w, h) * 2 // 3 if not text else min(w, h) // 2
        glyph = _emoji_glyph(emoji, icon_size)
        if glyph is not None:
            image.paste(glyph, ((w - icon_size) // 2, 2), glyph)
            emoji_h = icon_size + 2

    if not text:
        return PILHelper.to_native_format(deck, image)

    draw = ImageDraw.Draw(image)
    if font_size is None:
        font = ImageFont.load_default()
        wrap_width = 10
        line_h = 12
    else:
        font = ImageFont.load_default(size=font_size)
        wrap_width = max(4, round(110 / font_size))
        line_h = round(font_size * 1.15)
    lines = textwrap.wrap(text, width=wrap_width)
    total_h = len(lines) * line_h
    available_h = h - emoji_h
    y = emoji_h + max(0, (available_h - total_h) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), line, font=font, fill=text_color)
        y += line_h
    return PILHelper.to_native_format(deck, image)
