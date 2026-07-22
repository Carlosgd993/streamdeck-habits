"""Fabrica que enruta cada habito de TickTick a su clase segun el tipo."""

from __future__ import annotations

from typing import Any

from habits.base import Habit
from habits.boolean import BooleanHabit
from habits.real import RealHabit

_TYPE_MAP: dict[str, type[Habit]] = {
    "Boolean": BooleanHabit,
    "Real": RealHabit,
}


def build_habit(data: dict[str, Any]) -> Habit:
    """Construye el objeto ``Habit`` adecuado para un habito de TickTick.

    Los tipos desconocidos caen por defecto en ``BooleanHabit``.

    Args:
        data: Diccionario del habito devuelto por la API de TickTick.

    Returns:
        La instancia de ``Habit`` correspondiente al tipo del habito.
    """
    habit_cls = _TYPE_MAP.get(data.get("type"), BooleanHabit)
    return habit_cls(data)
