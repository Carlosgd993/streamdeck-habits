"""Clasificacion y registro de fallos: en tecla (errores de los proveedores de
datos, sean de habitos o de tareas) o en fichero (errores del propio
dispositivo Stream Deck)."""

from __future__ import annotations

import json
from datetime import datetime

from config import DEVICE_LOG, FAIL_LOG
from provider.base import ProviderAuthError, ProviderError, ProviderNetworkError


def classify(exc: Exception) -> tuple[str, str]:
    """Clasifica un fallo del ciclo segun donde debe reportarse.

    Args:
        exc: La excepcion capturada durante el ciclo o el checkin.

    Returns:
        ``("key", codigo)`` para errores de un proveedor de datos -- habitos o
        tareas, ambos usan la misma jerarquia ``Provider*`` -- (se muestran en
        tecla con ese codigo corto), o ``("file", "")`` para todo lo demas
        (errores del propio dispositivo Stream Deck, que nunca se muestran en
        tecla y van a un log de fichero plano, asi que no tienen codigo).
    """
    if isinstance(exc, ProviderAuthError):
        return "key", "AUTH"
    if isinstance(exc, ProviderNetworkError):
        return "key", "NET"
    if isinstance(exc, ProviderError):
        return "key", "API"
    return "file", ""


def log_failure(item_id: str, detail: str, kind: str = "habit") -> None:
    """Anade una linea JSON a checkin_failures.log con lo que fallo y el detalle.

    Args:
        item_id: Id del habito, de la tarea o de la plantilla sobre el que
            fallo la escritura.
        detail: Mensaje de la excepcion.
        kind: ``"habit"``, ``"task"`` o ``"template"``, para saber a que se
            refiere ``item_id``.
    """
    with open(FAIL_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "kind": kind,
            "item_id": item_id,
            "detail": detail,
        }) + "\n")


def log_device_error(detail: str) -> None:
    """Anade una linea de texto plano a device_errors.log con marca de tiempo."""
    with open(DEVICE_LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()} {detail}\n")
