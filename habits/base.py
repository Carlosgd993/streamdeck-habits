"""Clase base abstracta para los tipos de habito de TickTick."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Habit(ABC):
    """Habito de TickTick, envoltorio sobre su representacion JSON cruda.

    Attributes:
        data: El habito tal cual lo serializa la API de TickTick.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        """Inicializa el habito.

        Args:
            data: Diccionario del habito devuelto por la API de TickTick.
        """
        self.data = data

    @property
    def id(self) -> str:
        """Id unico del habito."""
        return self.data["id"]

    @property
    def name(self) -> str:
        """Nombre del habito (el que se muestra en la tecla)."""
        return self.data["name"]

    def is_done_today(self, done_ids: set[str]) -> bool:
        """Indica si el habito esta completado hoy.

        Args:
            done_ids: Ids de habitos con checkin completado hoy.
        """
        return self.id in done_ids

    def is_locked(self, done_ids: set[str]) -> bool:
        """Indica si la tecla debe ignorar nuevas pulsaciones hoy.

        Por defecto nunca se bloquea: los habitos booleanos siguen aceptando
        pulsaciones repetidas (el checkin es idempotente). Los habitos que
        acumulan progreso (p.ej. ``RealHabit``) lo sobrescriben para bloquear
        la tecla al alcanzar el objetivo.

        Args:
            done_ids: Ids de habitos con checkin completado hoy.
        """
        return False

    def display_label(self, current_value: float | None = None) -> str:
        """Texto a mostrar en la tecla.

        Por defecto el nombre del habito. Los habitos cuantificables lo
        sobrescriben para anadir el progreso (p.ej. ``"3/8 Cups"``).

        Args:
            current_value: Progreso acumulado hoy, si se conoce.
        """
        return self.name

    @abstractmethod
    def build_checkin_payload(self, stamp: int, current_value: float = 0.0) -> dict[str, Any]:
        """Devuelve el dict que se envia como body al hacer checkin.

        Args:
            stamp: Dia del checkin en formato ``YYYYMMDD``.
            current_value: Progreso acumulado hoy antes de esta pulsacion
                (0.0 si aun no hay checkin). Los habitos booleanos lo ignoran.
        """
