"""Carga del token de acceso a la API de TickTick desde el fichero .env."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from config import ENV_FILE


def get_token() -> str | None:
    """Carga el .env y devuelve el token de acceso de TickTick.

    Returns:
        El valor de ``TICKTICK_ACCESS_TOKEN``, o ``None`` si no esta definido.
    """
    load_dotenv(ENV_FILE)
    return os.environ.get("TICKTICK_ACCESS_TOKEN")
