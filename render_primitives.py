import textwrap

from PIL import ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper


def solid_tile(deck, color):
    image = PILHelper.create_image(deck, background=color)
    return PILHelper.to_native_format(deck, image)


def text_tile(deck, color, text):
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
