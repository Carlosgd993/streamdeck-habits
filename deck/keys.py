"""Efecto lateral de apagado de la Raspberry Pi, invocado desde el submenu
Sistema del menu de navegacion (ver ``core.screens.SYSTEM_ENTRIES`` y el
dispatcher de ``orchestrator.py``)."""

from __future__ import annotations

import subprocess

import core.health as health

# Comando de apagado ordenado. Requiere una regla NOPASSWD en sudoers para el
# usuario del servicio (ver CLAUDE.md), ya que el proceso corre sin sudo; sin
# esa regla el comando fallara y quedara registrado en DEVICE_LOG.
_SHUTDOWN_CMD = ["sudo", "-n", "shutdown", "-h", "now"]


def shutdown_pi() -> None:
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
