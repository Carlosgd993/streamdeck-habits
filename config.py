"""Constantes compartidas del daemon: rutas de ficheros, teclas reservadas,
intervalo de refresco y colores de las teclas."""

import os

BASE_DIR = "/opt/streamdeck-habits"
ENV_FILE = os.path.join(BASE_DIR, ".env")
MAP_FILE = os.path.join(BASE_DIR, "habit_key_map.json")
FAIL_LOG = os.path.join(BASE_DIR, "checkin_failures.log")
DEVICE_LOG = os.path.join(BASE_DIR, "device_errors.log")

RESERVED_KEYS = {0, 5, 10}
KEY_REFRESH = 0  # tecla reservada que fuerza un refresco inmediato desde la API
ALL_KEYS = set(range(15))
AVAILABLE_KEYS = sorted(ALL_KEYS - RESERVED_KEYS)

REFRESH_SECONDS = 900  # 15 min

COLOR_HABIT_PENDING = (40, 60, 200)   # azul: hoy no hecho
COLOR_HABIT_DONE = (30, 200, 60)      # verde: hoy hecho
COLOR_ERROR = (200, 40, 40)           # rojo: fallo al enviar
COLOR_RESERVED = (25, 25, 25)
COLOR_EMPTY = (0, 0, 0)
