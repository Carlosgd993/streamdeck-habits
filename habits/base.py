from abc import ABC, abstractmethod


class Habit(ABC):
    def __init__(self, data):
        self.data = data

    @property
    def id(self):
        return self.data["id"]

    @property
    def name(self):
        return self.data["name"]

    def is_done_today(self, done_ids):
        return self.id in done_ids

    @abstractmethod
    def build_checkin_payload(self, stamp):
        """Devuelve el dict que se envia como body al hacer checkin."""
