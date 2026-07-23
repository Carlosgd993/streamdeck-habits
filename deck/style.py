"""Estilo visual de las teclas del Stream Deck: colores y tamanos de fuente.

Estas constantes pertenecen a la capa de pintado (``deck/``): describen como se
ve una tecla, no la logica de habitos ni la del proveedor de datos.
"""

from __future__ import annotations

COLOR_HABIT_PENDING = (255, 255, 255)  # blanco: hoy no hecho, resalta sobre el resto
COLOR_HABIT_DONE = (55, 55, 55)        # gris oscuro apagado: hoy hecho, pasa desapercibido
COLOR_ERROR = (200, 40, 40)            # rojo: fallo al enviar
COLOR_RESERVED = (25, 25, 25)
COLOR_EMPTY = (0, 0, 0)

COLOR_TEXT_HABIT_PENDING = (0, 0, 0)        # texto negro sobre fondo blanco
COLOR_TEXT_HABIT_DONE = (110, 110, 110)     # texto tambien atenuado, refuerza el bajo contraste
FONT_SIZE_HABIT_PENDING = 16                # texto mas grande para resaltar lo pendiente
