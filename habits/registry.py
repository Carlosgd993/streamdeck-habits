"""Fabrica que enruta cada habito de TickTick a su clase segun el tipo."""

from __future__ import annotations

from typing import Any

from habits.base import Habit
from habits.boolean import BooleanHabit

# habits/real.py (RealHabit) todavia no esta implementado (ver ese fichero).
# Hasta entonces, los habitos de tipo "Real" se enrutan a BooleanHabit para
# no romper el checkin de habitos que hoy ya funcionan en produccion.
_TYPE_MAP: dict[str, type[Habit]] = {
    "Boolean": BooleanHabit,
    "Real": BooleanHabit,  # TODO: cambiar a RealHabit cuando este implementado
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
