"""Clasificacion y registro de fallos: en tecla (API TickTick) o en fichero
(errores del propio dispositivo Stream Deck)."""

from __future__ import annotations

import json
from datetime import datetime

from config import DEVICE_LOG, FAIL_LOG
from ticktick_client import TickTickAPIError, TickTickAuthError, TickTickError, TickTickNetworkError


def classify(exc: Exception) -> tuple[str, str | None]:
    """Clasifica un fallo del ciclo segun donde debe reportarse.

    Args:
        exc: La excepcion capturada durante el ciclo o el checkin.

    Returns:
        ``("key", codigo)`` para errores de la API de TickTick (se muestran en
        tecla con ese codigo corto), o ``("file", None)`` para todo lo demas
        (errores del propio dispositivo Stream Deck, que nunca se muestran en
        tecla y van a un log de fichero plano).
    """
    if isinstance(exc, TickTickAuthError):
        return "key", "T401"
    if isinstance(exc, TickTickNetworkError):
        return "key", "TNET"
    if isinstance(exc, (TickTickAPIError, TickTickError)):
        return "key", "TERR"
    return "file", None


def log_failure(habit_id: str, detail: str) -> None:
    """Anade una linea JSON a checkin_failures.log con el habito y el detalle."""
    with open(FAIL_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "habit_id": habit_id,
            "detail": detail,
        }) + "\n")


def log_device_error(detail: str) -> None:
    """Anade una linea de texto plano a device_errors.log con marca de tiempo."""
    with open(DEVICE_LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()} {detail}\n")
