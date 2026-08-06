"""Estilo visual de las teclas del Stream Deck: colores y tamanos de fuente.

Estas constantes pertenecen a la capa de pintado (``deck/``): describen como se
ve una tecla, no la logica de habitos ni la del proveedor de datos.
"""

from __future__ import annotations

COLOR_HABIT_PENDING = (255, 255, 255)  # blanco: hoy no hecho, resalta sobre el resto
COLOR_HABIT_DONE = (55, 55, 55)        # gris oscuro apagado: hoy hecho, pasa desapercibido
COLOR_ERROR = (200, 40, 40)            # rojo: fallo al enviar
COLOR_EMPTY = (0, 0, 0)
COLOR_SHUTDOWN = (120, 15, 15)          # rojo oscuro de aviso: accion destructiva (apagar la Pi)

# --- Navegacion (menu, submenu Sistema, paginacion) --------------------------
COLOR_MENU = (25, 25, 25)              # tecla 0: aspecto fijo en cualquier pantalla
COLOR_NEUTRAL = (25, 25, 25)           # teclas 5/10 cuando no hace falta paginar
COLOR_NAV = (40, 90, 150)              # boton generico de menu/submenu (azul, distinto de habitos/tareas)
COLOR_TEXT_NAV = (255, 255, 255)
COLOR_ARROW = (55, 55, 90)             # flecha de paginacion activa: distinto de COLOR_NEUTRAL
COLOR_TEXT_ARROW = (255, 255, 255)     # a proposito, para notar que hay mas paginas
FONT_SIZE_NAV = 14

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

# --- Plantillas (vista "Crear") ---------------------------------------------
# Un morado que no choca con ninguno de los anteriores: no es un boton de
# navegacion (COLOR_NAV es azul), no es una tarea (blanco/verde/amarillo/rojo)
# ni un habito (blanco/gris). Una tecla morada significa siempre "esto crea algo".
COLOR_TEMPLATE = (110, 70, 160)
COLOR_TEXT_TEMPLATE = (255, 255, 255)

# Plantilla que ya tiene una ocurrencia pendiente: el mismo gris que un habito
# ya hecho, y por el mismo motivo -- "esto ya esta, no hace falta tocarlo". Ahi
# la tecla ademas no hace nada (ver core.screens.resolve_press).
COLOR_TEMPLATE_PENDING = COLOR_HABIT_DONE
COLOR_TEXT_TEMPLATE_PENDING = COLOR_TEXT_HABIT_DONE
FONT_SIZE_TEMPLATE = 14

# --- Teclado numerico (entrada manual de un habito, p.ej. "Peso") -----------
# Familia nueva, sin chocar con ninguna de las anteriores: no es un habito
# (blanco/gris), ni una tarea (prioridad), ni una plantilla (morado), ni
# navegacion de menu (COLOR_NAV es azul, pero "Salir" lo reutiliza tal cual:
# mismo rol de "esto te saca de aqui" que la tecla de menu).
COLOR_NUMERIC = (45, 45, 60)               # digitos y "."
COLOR_TEXT_NUMERIC = (255, 255, 255)
COLOR_NUMERIC_BACKSPACE = (200, 130, 40)   # ambar: distinto de COLOR_ERROR
COLOR_TEXT_NUMERIC_BACKSPACE = (0, 0, 0)
COLOR_CONFIRM = (30, 160, 70)              # verde estatico del boton OK (no el
                                            # verde vivo de acuse COLOR_TASK_SENDING)
COLOR_TEXT_CONFIRM = (255, 255, 255)
COLOR_NUMERIC_DISPLAY = (20, 20, 30)       # "pantalla" del valor tecleado
COLOR_TEXT_NUMERIC_DISPLAY = (255, 255, 255)
FONT_SIZE_NUMERIC = 20
FONT_SIZE_NUMERIC_DISPLAY = 14
