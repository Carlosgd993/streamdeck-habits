"""Extraccion de emoji de texto libre (p.ej. el icono de un habito).

Modulo sin dependencias de Pillow/StreamDeck ni de ningun proveedor concreto,
para poder usarse desde cualquier adaptador de ``provider/`` que necesite
separar el emoji de un icono textual.
"""

from __future__ import annotations

import re

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001faff"  # simbolos/pictogramas misc, emoticonos, transporte, suplementarios...
    "\U00002600-\U000027bf"  # simbolos misc y dingbats (☀ ✅ ✂ etc.)
    "\U0001f1e6-\U0001f1ff"  # banderas (pares de letras regionales)
    "\U00002b00-\U00002bff"  # flechas/estrellas adicionales (⭐ ➡ etc.)
    "\U0000fe0f"  # variation selector-16 (fuerza presentacion emoji)
    "\U0000200d"  # zero width joiner (secuencias compuestas, p.ej. familias)
    "]+"
)


def extract_emoji(text: str) -> tuple[str, str]:
    """Separa el primer emoji (o secuencia de emoji) de un texto.

    Args:
        text: Texto de entrada, p.ej. el icono textual de un habito.

    Returns:
        Tupla ``(emoji, resto)``: ``emoji`` es la subcadena emoji encontrada
        (vacia si no hay ninguna) y ``resto`` es ``text`` sin esa subcadena,
        con espacios sobrantes recortados.
    """
    match = _EMOJI_PATTERN.search(text)
    if not match:
        return "", text
    emoji = match.group(0)
    rest = (text[: match.start()] + text[match.end() :]).strip()
    return emoji, rest
