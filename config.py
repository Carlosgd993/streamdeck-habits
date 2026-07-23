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
KEY_REFRESH = 0  # tecla reservada que fuerza un refresco inmediato desde el proveedor
ALL_KEYS = set(range(15))
AVAILABLE_KEYS = sorted(ALL_KEYS - RESERVED_KEYS)

REFRESH_SECONDS = 900  # 15 min
