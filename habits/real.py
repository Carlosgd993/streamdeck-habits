"""Habito cuantificable (tipo 'Real' en TickTick): acumula valor con cada
pulsacion (p.ej. +1 vaso de agua) hasta alcanzar un objetivo."""

from __future__ import annotations

from typing import Any

from habits.base import Habit


def _format_number(value: float) -> str:
    """Formatea un numero sin ``.0`` cuando es entero (``8`` en vez de ``8.0``)."""
    return str(int(value)) if value == int(value) else f"{value:g}"


class RealHabit(Habit):
    """Habito cuantificable; cada checkin fija el valor TOTAL acumulado del dia.

    La API de TickTick no ofrece un checkin incremental: cada POST hace
    upsert del ``value`` de ese ``stamp``. Por eso ``build_checkin_payload``
    necesita el progreso actual (``current_value``) para poder sumarle
    ``step`` antes de reenviarlo.
    """

    @property
    def goal(self) -> float:
        """Objetivo diario del habito (p.ej. ``8.0`` vasos)."""
        return float(self.data.get("goal", 1.0))

    @property
    def step(self) -> float:
        """Cantidad que suma cada pulsacion (p.ej. ``1.0``)."""
        return float(self.data.get("step", 1.0))

    @property
    def unit(self) -> str:
        """Unidad del habito (p.ej. ``"Cups"``); vacia si no esta definida."""
        return str(self.data.get("unit", ""))

    def is_locked(self, done_ids: set[str]) -> bool:
        """Bloquea la tecla una vez alcanzado el objetivo (no se supera el goal)."""
        return self.is_done_today(done_ids)

    def display_label(self, current_value: float | None = None) -> str:
        """Progreso acumulado hoy, sin el nombre (p.ej. ``"3/8 Cups"``).

        El nombre se omite a proposito: el emoji del habito (ver
        ``Habit.emoji``) ya identifica de que habito se trata, y en la
        tecla apenas hay sitio para nombre + progreso legibles.
        """
        value = current_value if current_value is not None else 0.0
        progress = f"{_format_number(value)}/{_format_number(self.goal)}"
        if self.unit:
            progress = f"{progress} {self.unit}"
        return progress

    def build_checkin_payload(self, stamp: int, current_value: float = 0.0) -> dict[str, Any]:
        """Suma ``step`` al progreso actual, sin superar ``goal``."""
        new_value = min(current_value + self.step, self.goal)
        return {"stamp": stamp, "value": new_value, "goal": self.goal}
