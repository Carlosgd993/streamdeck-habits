from habits.base import Habit


class BooleanHabit(Habit):
    def build_checkin_payload(self, stamp):
        return {"stamp": stamp, "value": 1.0, "goal": 1.0}
