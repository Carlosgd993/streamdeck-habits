"""Constantes compartidas del daemon: rutas de ficheros, layout de teclas del
Stream Deck e intervalo de refresco. Agnosticas del proveedor de datos.

Los colores y tamanos de fuente de las teclas viven en ``deck/style.py`` (capa
de pintado), no aqui.
"""

import os

BASE_DIR = "/opt/streamdeck-habits"
ENV_FILE = os.path.join(BASE_DIR, ".env")
MAP_FILE = os.path.join(BASE_DIR, "habit_key_map.json")
FAIL_LOG = os.path.join(BASE_DIR, "checkin_failures.log")
DEVICE_LOG = os.path.join(BASE_DIR, "device_errors.log")

RESERVED_KEYS = {0, 5, 10}
KEY_MENU = 0  # tecla reservada que abre el menu principal desde cualquier pantalla
KEY_PAGE_PREV = 5  # tecla reservada: flecha "<-" cuando la pantalla activa pagina
KEY_PAGE_NEXT = 10  # tecla reservada: flecha "->" cuando la pantalla activa pagina
ALL_KEYS = set(range(15))
AVAILABLE_KEYS = sorted(ALL_KEYS - RESERVED_KEYS)
PAGE_SIZE = len(AVAILABLE_KEYS)  # items por pagina en cualquier pantalla (menu, sistema o vista)

REFRESH_SECONDS = 900  # 15 min
AUTO_RETURN_SECONDS = 300  # 5 min sin pulsar fuera de la vista "Hoy" -> vuelve sola a "Hoy"
STANDBY_SECONDS = 1800  # 30 min sin pulsar nada -> stand by (pantalla apagada, sin refrescos)
LONG_PRESS_SECONDS = 0.6  # mantener pulsado un habito/tarea este tiempo abre su menu de opciones

RESTORE_COOLDOWN_SECONDS = 1800  # 30 min minimo entre intentos de reactivar un proyecto Supabase pausado

TIMER_TICK_SECONDS = 1  # repinta (sin refetch, calculo local a partir de running_timer_ref) mientras haya
                         # un cronometro corriendo, para que el tiempo transcurrido se vea al segundo
TIMER_SYNC_SECONDS = 60  # refetch real de get_running_timer() mientras haya un cronometro corriendo, para
                          # corregir el calculo local (deriva de reloj, cambio hecho por otro cliente) sin
                          # esperar a REFRESH_SECONDS (15 min)
