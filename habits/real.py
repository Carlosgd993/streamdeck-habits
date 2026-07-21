from habits.base import Habit


class RealHabit(Habit):
    """Esqueleto para habitos cuantificables (tipo 'Real' en TickTick).

    Todavia no implementado -- se define en una sesion posterior. No esta
    conectado desde habits/registry.py: por ahora los habitos 'Real' se
    siguen tratando como BooleanHabit para no romper produccion.
    """

    def is_done_today(self, done_ids):
        raise NotImplementedError

    def build_checkin_payload(self, stamp):
        raise NotImplementedError
