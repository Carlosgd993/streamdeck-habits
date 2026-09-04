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

# Un habito de solo registro (vista "Logs") no tiene estado pendiente/hecho
# que pintar en blanco/gris: usa el color propio que trae la base
# (``habits.color``), y este es solo el fallback para el que no tenga uno
# definido -- un verde azulado que no choca con ninguna otra familia
# (blanco/gris de habito, prioridades de tarea, morado de plantilla, azul de
# nav, familia del teclado numerico, naranja de skip). Ver
# ``deck.renderer.render_habit``.
COLOR_HABIT_LOG_DEFAULT = (30, 140, 140)

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

# --- Menu de opciones de un habito/tarea (mantener pulsado) -----------------
# Familia nueva: ni habito/tarea/plantilla, ni teclado numerico, ni stand by.
# "Volver" reutiliza COLOR_NAV (mismo rol que "Salir" en el teclado numerico:
# esto te saca de aqui sin tocar nada). El mensaje informativo usa un gris
# azulado propio, deliberadamente apagado, para no parecer una tecla de
# contenido pulsable -- de momento no hace nada al pulsarla.
COLOR_OPTIONS_MESSAGE = (35, 45, 55)
COLOR_TEXT_OPTIONS_MESSAGE = (200, 200, 200)
FONT_SIZE_OPTIONS = 13

# "Skip" (omitir una tarea, solo en TASK_OPTIONS_LAYOUT): naranja propio,
# distinto de COLOR_ERROR/COLOR_SHUTDOWN (no es un fallo ni algo tan
# irreversible como apagar la Pi) y de COLOR_TASK_BY_PRIORITY (no es una
# prioridad).
COLOR_TASK_SKIP = (190, 110, 20)
COLOR_TEXT_TASK_SKIP = (255, 255, 255)

# "+1/+3/+5/+Paso" y "-1/-3/-5/-Paso" (solo en REAL_HABIT_OPTIONS_LAYOUT):
# verde para sumar, granate para restar -- un par propio que no choca con
# ninguno de los verdes/rojos ya usados en esta pantalla o en otras
# (COLOR_TASK_SENDING, COLOR_CONFIRM, COLOR_TASK_BY_PRIORITY, COLOR_ERROR,
# COLOR_SHUTDOWN, COLOR_NUMERIC_BACKSPACE de "Deshacer", que convive en la
# misma pantalla). deck.renderer.render_option_entry elige entre los dos
# solo mirando el signo de OptionEntry.amount, sin distinguir "add_value" de
# "add_step".
COLOR_HABIT_ADD = (45, 150, 100)
COLOR_TEXT_HABIT_ADD = (255, 255, 255)
COLOR_HABIT_SUBTRACT = (150, 60, 80)
COLOR_TEXT_HABIT_SUBTRACT = (255, 255, 255)

# --- Cronometros (vista "Cronometros" + opcion "timer" de TASK_OPTIONS_LAYOUT) ---
# Familia de ESTADO, no de identidad: con pocas etiquetas la posicion en la
# rejilla ya dice cual es cual, y lo urgente de comunicar en una tecla es si
# esta corriendo o no -- por eso el deck IGNORA TimerLabel.color (reservado
# para un cliente de analitica futuro, ver provider.base.TimerLabel). Rosa/
# magenta para "corriendo": no coincide con ningun rojo ya usado (COLOR_ERROR,
# COLOR_SHUTDOWN, prioridad 5 de tarea). Turquesa apagado para "parado":
# distinto de COLOR_TEMPLATE (morado) y COLOR_NAV/COLOR_ARROW (azul) -- mas
# oscuro que COLOR_HABIT_LOG_DEFAULT (tambien teal) para no confundirse con
# "Logs" al navegar entre las dos vistas, aunque nunca coincidan en pantalla.
COLOR_TIMER_RUNNING = (210, 30, 100)
COLOR_TEXT_TIMER_RUNNING = (255, 255, 255)
COLOR_TIMER_STOPPED = (55, 110, 130)
COLOR_TEXT_TIMER_STOPPED = (255, 255, 255)
FONT_SIZE_TIMER = 13

# --- Stand by ---------------------------------------------------------------
# La pantalla de suspension se ve con el brillo al minimo
# (``deck.session.BRIGHTNESS_STANDBY``), asi que aqui no se busca contraste
# entre teclas sino que el conjunto quede casi negro y solo se distinga el
# icono: fondo negro como una tecla vacia, y el emoji como unico elemento
# encendido. Que se vea *algo* es justo lo que separa "suspendida" de
# "apagada"/"colgada" a simple vista.
COLOR_STANDBY = COLOR_EMPTY
COLOR_TEXT_STANDBY = (110, 110, 110)
FONT_SIZE_STANDBY = 12
