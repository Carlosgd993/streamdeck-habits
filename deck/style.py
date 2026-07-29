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
COLOR_SHUTDOWN = (120, 15, 15)          # rojo oscuro de aviso: accion destructiva (apagar la Pi)

COLOR_TEXT_HABIT_PENDING = (0, 0, 0)        # texto negro sobre fondo blanco
COLOR_TEXT_HABIT_DONE = (110, 110, 110)     # texto tambien atenuado, refuerza el bajo contraste
COLOR_TEXT_SHUTDOWN = (255, 255, 255)
FONT_SIZE_HABIT_PENDING = 16                # texto mas grande para resaltar lo pendiente

# --- Tareas -----------------------------------------------------------------
# El color de una tarea lo da su prioridad. Los valores no son contiguos: la
# base solo admite 0 (ninguna), 1 (baja), 3 (media) y 5 (alta). Una prioridad
# desconocida cae a la entrada 0 (ver deck/renderer.py).
COLOR_TASK_BY_PRIORITY = {
    0: (255, 255, 255),  # blanca
    1: (60, 180, 75),    # verde
    3: (240, 200, 40),   # amarilla
    5: (215, 60, 50),    # roja -- distinta de COLOR_ERROR a proposito, para que
}                        # una tarea urgente no se confunda con una tecla en error

COLOR_TEXT_TASK_BY_PRIORITY = {
    0: (0, 0, 0),
    1: (0, 0, 0),
    3: (0, 0, 0),
    5: (255, 255, 255),  # el unico fondo lo bastante oscuro para pedir texto claro
}

COLOR_TASK_SENDING = (0, 230, 60)   # verde vivo: pulsacion registrada, peticion en vuelo. Mas
                                    # brillante que el verde de prioridad 1 para que se distinga
                                    # tambien sobre esas teclas
FONT_SIZE_TASK = 14
