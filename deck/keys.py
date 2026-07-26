"""Gestion de las teclas reservadas (0, 5, 10): refresco manual y apagado."""

from __future__ import annotations

import subprocess
from threading import Event
from typing import Any

import core.health as health
from config import KEY_REFRESH, KEY_SHUTDOWN, RESERVED_KEYS
from deck.renderer import render_reserved_key

# Comando de apagado ordenado. Requiere una regla NOPASSWD en sudoers para el
# usuario del servicio (ver CLAUDE.md), ya que el proceso corre sin sudo; sin
# esa regla el comando fallara y quedara registrado en DEVICE_LOG.
_SHUTDOWN_CMD = ["sudo", "-n", "shutdown", "-h", "now"]


def render_reserved_keys(deck: Any) -> None:
    """Pinta las teclas reservadas (0, 5, 10), cada una con su aspecto propio
    via ``renderer.render_reserved_key`` (la 5 sigue siendo placeholder gris,
    libre para configuracion futura)."""
    for key in RESERVED_KEYS:
        render_reserved_key(deck, key)


def _shutdown_pi() -> None:
    """Lanza el apagado ordenado de la Raspberry Pi. No bloquea el hilo de
    callbacks del Stream Deck mas alla de lo que tarde en arrancar el
    comando; si falla (p.ej. falta la regla sudoers) se registra en
    DEVICE_LOG en vez de intentar reflejarlo en tecla."""
    try:
        result = subprocess.run(_SHUTDOWN_CMD, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode != 0:
            health.log_device_error(f"Fallo al apagar la Pi (rc={result.returncode}): {result.stderr.strip()}")
    except Exception as exc:
        health.log_device_error(f"Fallo al apagar la Pi: {exc}")


def handle_key_press(key: int, refresh_event: Event) -> bool:
    """Gestiona la pulsacion de una tecla reservada. Devuelve True si la
    tecla era reservada (y por tanto ya gestionada u omitida a proposito),
    False si el llamador debe tratarla como una tecla de habito normal."""
    if key == KEY_REFRESH:
        refresh_event.set()
        return True
    if key == KEY_SHUTDOWN:
        _shutdown_pi()
        return True
    return key in RESERVED_KEYS
