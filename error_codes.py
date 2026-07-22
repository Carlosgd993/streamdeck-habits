"""Codigos de error cortos mostrados en tecla o en logs, con su descripcion."""

from __future__ import annotations

CODES: dict[str, str] = {
    "T401": "Token de TickTick invalido o caducado",
    "TNET": "Fallo de red hacia la API de TickTick",
    "TERR": "La API de TickTick devolvio algo inesperado",
    "KFUL": "Sin teclas libres para un habito nuevo",
}
