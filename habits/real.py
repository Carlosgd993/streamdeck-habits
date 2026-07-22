"""Habito cuantificable (tipo 'Real' en TickTick). Esqueleto sin implementar."""

from __future__ import annotations

from typing import Any

from habits.base import Habit


class RealHabit(Habit):
    """Esqueleto para habitos cuantificables (tipo 'Real' en TickTick).

    Todavia no implementado -- se define en una sesion posterior. No esta
    conectado desde ``habits/registry.py``: por ahora los habitos 'Real' se
    siguen tratando como ``BooleanHabit`` para no romper produccion.
    """

    def is_done_today(self, done_ids: set[str]) -> bool:
        raise NotImplementedError

    def build_checkin_payload(self, stamp: int) -> dict[str, Any]:
        raise NotImplementedError
