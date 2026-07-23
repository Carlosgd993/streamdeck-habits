"""Puerto abstracto del proveedor de habitos, agnostico de la API concreta.

Este modulo define TODO lo que el resto del proyecto (orquestador, dominio,
deck) necesita saber sobre "la API de habitos", sin acoplarse a ningun backend
en particular:

- ``HabitProvider``: la interfaz (puerto) que cualquier backend debe implementar.
- Jerarquia de excepciones agnostica (``ProviderError`` y subclases).
- El modelo de dominio ``Habit`` (y sus subtipos ``BooleanHabit``/``RealHabit``),
  construido desde campos ya parseados -- nunca desde el JSON crudo de un backend.
- ``Progress``: el progreso (value/goal) de un habito en un dia.

Para sustituir TickTick por otra API basta con escribir un adaptador nuevo que
implemente ``HabitProvider`` devolviendo objetos ``Habit``/``Progress`` de este
modulo y traduciendo sus fallos a las excepciones ``Provider*`` de aqui; el
resto del proyecto no necesita cambiar (ver ``provider/ticktick.py`` como
ejemplo de adaptador concreto).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


class ProviderError(Exception):
    """Base para cualquier fallo al hablar con el proveedor de habitos."""


class ProviderAuthError(ProviderError):
    """Credenciales invalidas o caducadas (p.ej. token expirado, 401)."""


class ProviderNetworkError(ProviderError):
    """Fallo de conexion (timeout, DNS, etc.) hacia el proveedor."""


class ProviderDataError(ProviderError):
    """El proveedor respondio algo distinto de lo esperado (status/JSON)."""


@dataclass(frozen=True)
class Progress:
    """Progreso de un habito en un dia concreto.

    Attributes:
        value: Progreso acumulado registrado ese dia.
        goal: Objetivo vigente cuando se registro ese progreso (no
            necesariamente el objetivo actual del habito).
    """

    value: float
    goal: float


class Habit(ABC):
    """Habito de seguimiento, agnostico del backend que lo origino.

    A diferencia del JSON crudo de una API concreta, un ``Habit`` expone solo
    campos y comportamiento de dominio. Cada adaptador de proveedor es
    responsable de mapear su representacion nativa a estos objetos.

    Attributes:
        id: Identificador unico del habito en el proveedor.
        name: Nombre legible del habito.
        emoji: Emoji elegido como icono, o cadena vacia si no tiene.
        order: Pista de orden para asignar teclas de forma estable (los
            habitos nuevos reclaman teclas en este orden). El adaptador la
            rellena desde el campo de orden que use su backend.
    """

    def __init__(self, id: str, name: str, emoji: str = "", order: int = 0) -> None:
        self.id = id
        self.name = name
        self.emoji = emoji
        self.order = order

    @property
    def goal(self) -> float:
        """Objetivo diario del habito. Por defecto ``1.0`` (habito booleano)."""
        return 1.0

    def is_done_today(self, done_ids: set[str]) -> bool:
        """Indica si el habito esta completado hoy.

        Args:
            done_ids: Ids de habitos con progreso completado hoy.
        """
        return self.id in done_ids

    def is_locked(self, done_ids: set[str]) -> bool:
        """Indica si la tecla debe ignorar nuevas pulsaciones hoy.

        Por defecto nunca se bloquea: los habitos booleanos siguen aceptando
        pulsaciones repetidas (el checkin es idempotente). Los habitos que
        acumulan progreso (``RealHabit``) lo sobrescriben para bloquear la
        tecla al alcanzar el objetivo.

        Args:
            done_ids: Ids de habitos con progreso completado hoy.
        """
        return False

    def display_label(self, current_value: float | None = None) -> str:
        """Texto a mostrar en la tecla (aparte del emoji, ver ``emoji``).

        Por defecto el nombre del habito. Los habitos cuantificables lo
        sobrescriben para mostrar el progreso en vez del nombre (p.ej.
        ``"3/8 Cups"``).

        Args:
            current_value: Progreso acumulado hoy, si se conoce.
        """
        return self.name

    @abstractmethod
    def next_value(self, current_value: float = 0.0) -> float:
        """Valor TOTAL que tendria el habito hoy tras una pulsacion.

        El dominio decide el valor objetivo (p.ej. sumar un paso hasta el
        goal); el adaptador de proveedor lo traduce a la llamada concreta de
        su API. El orquestador lo usa tanto para el checkin como para la
        actualizacion optimista de la tecla.

        Args:
            current_value: Progreso acumulado hoy antes de esta pulsacion
                (0.0 si aun no hay checkin). Los habitos booleanos lo ignoran.
        """


class BooleanHabit(Habit):
    """Habito booleano: hecho o no hecho, sin cantidad. Objetivo siempre cumplido."""

    def next_value(self, current_value: float = 0.0) -> float:
        """Siempre ``1.0``: pulsar marca el habito como cumplido ese dia."""
        return 1.0


class RealHabit(Habit):
    """Habito cuantificable: acumula ``step`` con cada pulsacion hasta ``goal``.

    Attributes:
        step: Cantidad que suma cada pulsacion (p.ej. ``1.0`` vaso).
        unit: Unidad del habito (p.ej. ``"Cups"``); vacia si no esta definida.
    """

    def __init__(
        self,
        id: str,
        name: str,
        emoji: str = "",
        order: int = 0,
        goal: float = 1.0,
        step: float = 1.0,
        unit: str = "",
    ) -> None:
        super().__init__(id, name, emoji, order)
        self._goal = goal
        self.step = step
        self.unit = unit

    @property
    def goal(self) -> float:
        """Objetivo diario del habito (p.ej. ``8.0`` vasos)."""
        return self._goal

    def is_locked(self, done_ids: set[str]) -> bool:
        """Bloquea la tecla una vez alcanzado el objetivo (no se supera el goal)."""
        return self.is_done_today(done_ids)

    def display_label(self, current_value: float | None = None) -> str:
        """Progreso acumulado hoy, sin el nombre (p.ej. ``"3/8 Cups"``).

        El nombre se omite a proposito: el emoji del habito ya identifica de
        que habito se trata, y en la tecla apenas hay sitio para nombre +
        progreso legibles.
        """
        value = current_value if current_value is not None else 0.0
        progress = f"{_format_number(value)}/{_format_number(self.goal)}"
        if self.unit:
            progress = f"{progress} {self.unit}"
        return progress

    def next_value(self, current_value: float = 0.0) -> float:
        """Suma ``step`` al progreso actual, sin superar ``goal``."""
        return min(current_value + self.step, self.goal)


def _format_number(value: float) -> str:
    """Formatea un numero sin ``.0`` cuando es entero (``8`` en vez de ``8.0``)."""
    return str(int(value)) if value == int(value) else f"{value:g}"


class HabitProvider(ABC):
    """Puerto: contrato que debe implementar cualquier backend de habitos.

    El orquestador depende solo de esta interfaz y de los tipos agnosticos de
    este modulo (``Habit``, ``Progress``, ``Provider*Error``). Sustituir de
    proveedor es implementar esta clase y cambiar una linea de construccion en
    el orquestador.
    """

    @abstractmethod
    def get_habits(self) -> list[Habit]:
        """Devuelve la lista de habitos del usuario.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def get_progress(self, habit_ids: list[str], day: date) -> dict[str, Progress]:
        """Devuelve el progreso de cada habito en un dia concreto.

        Un habito sin progreso registrado ese dia simplemente no aparece en el
        resultado (progreso 0 implicito).

        Args:
            habit_ids: Ids de los habitos a consultar.
            day: Dia a consultar.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def checkin(self, habit: Habit, day: date, value: float) -> None:
        """Registra el progreso total (``value``) de un habito en un dia.

        Args:
            habit: El habito sobre el que se registra el progreso.
            day: Dia del checkin.
            value: Progreso TOTAL acumulado a registrar ese dia (ya calculado
                por el llamador via ``habit.next_value``).

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """
