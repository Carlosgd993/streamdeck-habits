"""Habito booleano de TickTick: hecho o no hecho, sin cantidad."""

from __future__ import annotations

from typing import Any

from habits.base import Habit


class BooleanHabit(Habit):
    """Habito de tipo booleano; el checkin siempre marca cumplido el objetivo."""

    def build_checkin_payload(self, stamp: int) -> dict[str, Any]:
        """Devuelve un checkin que marca el habito como cumplido ese dia."""
        return {"stamp": stamp, "value": 1.0, "goal": 1.0}
