"""Codigos de error cortos mostrados en tecla o en logs, con su descripcion.

Los codigos son agnosticos del proveedor concreto de habitos: describen la
categoria del fallo (auth, red, datos, sin teclas), no el backend.
"""

from __future__ import annotations

CODES: dict[str, str] = {
    "AUTH": "Credenciales del proveedor de habitos invalidas o caducadas",
    "NET": "Fallo de red hacia el proveedor de habitos",
    "API": "El proveedor de habitos devolvio algo inesperado",
    "KFUL": "Sin teclas libres para un habito nuevo",
}
