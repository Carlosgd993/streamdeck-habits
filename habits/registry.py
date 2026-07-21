from habits.boolean import BooleanHabit

# habits/real.py (RealHabit) todavia no esta implementado (ver ese fichero).
# Hasta entonces, los habitos de tipo "Real" se enrutan a BooleanHabit para
# no romper el checkin de habitos que hoy ya funcionan en produccion.
_TYPE_MAP = {
    "Boolean": BooleanHabit,
    "Real": BooleanHabit,  # TODO: cambiar a RealHabit cuando este implementado
}


def build_habit(data):
    habit_cls = _TYPE_MAP.get(data.get("type"), BooleanHabit)
    return habit_cls(data)
