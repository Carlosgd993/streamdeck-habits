"""Puertos abstractos de los proveedores de datos, agnosticos de la API concreta.

Este modulo define TODO lo que el resto del proyecto (orquestador, dominio,
deck) necesita saber sobre "la API", sin acoplarse a ningun backend en
particular:

- ``HabitProvider``: el puerto de habitos que cualquier backend debe implementar.
- ``TaskProvider``: el puerto de tareas, deliberadamente **separado** del
  anterior: un backend de habitos no tiene por que servir tambien tareas, y al
  reves. Un adaptador concreto puede implementar los dos (es lo que hace
  ``provider/supabase.py``, porque ambos salen del mismo contrato).
- Jerarquia de excepciones agnostica (``ProviderError`` y subclases), compartida
  por los dos puertos.
- Los modelos de dominio ``Habit`` (y sus subtipos ``BooleanHabit``/``RealHabit``)
  y ``Task``, construidos desde campos ya parseados -- nunca desde el JSON crudo
  de un backend.

La logica de negocio (que dia es hoy, cual es el siguiente valor de un habito,
que tareas estan pendientes) vive en la base de datos; estos puertos y sus
adaptadores son un renderizador tonto sobre lo que la base ya calculo.

Para sustituir de backend basta con escribir un adaptador nuevo que implemente
el puerto correspondiente devolviendo objetos de este modulo y traduciendo sus
fallos a las excepciones ``Provider*`` de aqui; el resto del proyecto no
necesita cambiar (ver ``provider/supabase.py`` como ejemplo de adaptador
concreto).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderError(Exception):
    """Base para cualquier fallo al hablar con el proveedor de habitos."""


class ProviderAuthError(ProviderError):
    """Credenciales invalidas o caducadas (p.ej. token expirado, 401)."""


class ProviderNetworkError(ProviderError):
    """Fallo de conexion (timeout, DNS, etc.) hacia el proveedor."""


class ProviderDataError(ProviderError):
    """El proveedor respondio algo distinto de lo esperado (status/JSON)."""


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
        current_value: Progreso acumulado hoy, ya calculado por el proveedor.
            Puede superar ``goal`` (p.ej. ``10.0`` con un objetivo de ``8.0``):
            es un estado valido, no un error.
    """

    def __init__(self, id: str, name: str, emoji: str = "", order: int = 0, current_value: float = 0.0) -> None:
        self.id = id
        self.name = name
        self.emoji = emoji
        self.order = order
        self.current_value = current_value

    @property
    def goal(self) -> float:
        """Objetivo diario del habito. Por defecto ``1.0`` (habito booleano)."""
        return 1.0

    @property
    def is_done(self) -> bool:
        """Indica si el habito alcanzo hoy su objetivo (``current_value >= goal``).

        No implica que la tecla deje de aceptar pulsaciones: un habito
        cuantificable sigue sumando progreso por encima del objetivo.
        """
        return self.current_value >= self.goal

    @abstractmethod
    def display_label(self) -> str:
        """Texto a mostrar en la tecla (aparte del emoji, ver ``emoji``)."""


class BooleanHabit(Habit):
    """Habito booleano: hecho o no hecho, sin cantidad. Objetivo siempre cumplido."""

    def display_label(self) -> str:
        """El nombre del habito (no hay progreso cuantificable que mostrar)."""
        return self.name


class RealHabit(Habit):
    """Habito cuantificable: acumula ``step`` con cada pulsacion, sin tope.

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
        current_value: float = 0.0,
        goal: float = 1.0,
        step: float = 1.0,
        unit: str = "",
    ) -> None:
        super().__init__(id, name, emoji, order, current_value)
        self._goal = goal
        self.step = step
        self.unit = unit

    @property
    def goal(self) -> float:
        """Objetivo diario del habito (p.ej. ``8.0`` vasos)."""
        return self._goal

    def display_label(self) -> str:
        """Progreso acumulado hoy, sin el nombre (p.ej. ``"3/8 Cups"``).

        El nombre se omite a proposito: el emoji del habito ya identifica de
        que habito se trata, y en la tecla apenas hay sitio para nombre +
        progreso legibles. Puede superar el objetivo (p.ej. ``"10/8 Cups"``).
        """
        progress = f"{_format_number(self.current_value)}/{_format_number(self.goal)}"
        if self.unit:
            progress = f"{progress} {self.unit}"
        return progress


def _format_number(value: float) -> str:
    """Formatea un numero sin ``.0`` cuando es entero (``8`` en vez de ``8.0``)."""
    return str(int(value)) if value == int(value) else f"{value:g}"


TITLE_MAX_CHARS = 32
"""Longitud maxima del titulo de una tarea en una tecla.

A partir de ahi el texto ya no cabe (el pintado envuelve en lineas cortas) y lo
que sobra se recorta con una elipsis en vez de desbordarse en silencio."""


class Task:
    """Ocurrencia de tarea pendiente, agnostica del backend que la origino.

    A diferencia de ``Habit`` no es abstracta ni tiene subtipos: una tarea solo
    esta pendiente o deja de existir, no hay un estado "hecha" que pintar.

    Attributes:
        id: Identificador unico de la ocurrencia en el proveedor. Es el que hay
            que enviar para cerrarla.
        title: Titulo legible de la tarea, ya sin el emoji si lo llevaba.
        emoji: Emoji del titulo para usar como icono, o cadena vacia. Las tareas
            no tienen campo de icono propio (al contrario que los habitos), pero
            es habitual escribirlo dentro del titulo: el adaptador lo separa
            para que se pinte como icono a color en vez de como un cuadro vacio
            con la fuente de texto.
        priority: Prioridad declarada. Los valores no son contiguos:
            ``0`` (ninguna), ``1`` (baja), ``3`` (media), ``5`` (alta). Decide
            el color de la tecla.
        overdue: Si la tarea ya vencio y arrastra de un dia anterior.
        due_day: Dia de vencimiento en formato ``YYYY-MM-DD``, ya normalizado a
            la zona horaria correcta por el proveedor. Vacio si no lo trae.
    """

    def __init__(
        self,
        id: str,
        title: str,
        emoji: str = "",
        priority: int = 0,
        overdue: bool = False,
        due_day: str = "",
    ) -> None:
        self.id = id
        self.title = title
        self.emoji = emoji
        self.priority = priority
        self.overdue = overdue
        self.due_day = due_day

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el titulo, recortado si no cabe.

        El emoji va aparte (ver ``emoji``), como en los habitos.
        """
        if len(self.title) <= TITLE_MAX_CHARS:
            return self.title
        return self.title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


class HabitProvider(ABC):
    """Puerto: contrato que debe implementar cualquier backend de habitos.

    El orquestador depende solo de esta interfaz y de los tipos agnosticos de
    este modulo (``Habit``, ``Provider*Error``). Sustituir de proveedor es
    implementar esta clase y cambiar una linea de construccion en el
    orquestador.
    """

    @abstractmethod
    def get_habits(self) -> list[Habit]:
        """Devuelve los habitos del usuario con su progreso de hoy ya incluido.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def step(self, habit: Habit) -> float:
        """Avanza un paso el progreso de hoy de ``habit``.

        El proveedor decide el nuevo valor (booleano -> salta a ``goal``,
        cuantificable -> suma ``step`` sin tope); este metodo solo lo ejecuta
        y devuelve el resultado para que el llamador repinte la tecla.

        Args:
            habit: El habito sobre el que avanzar.

        Returns:
            El nuevo valor TOTAL acumulado hoy.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def undo(self, habit: Habit) -> float:
        """Retrocede el progreso de hoy de ``habit`` (pulsacion erronea).

        Es la operacion inversa de ``step`` y, como aquella, el valor nuevo lo
        decide el proveedor (booleano -> vuelve a ``0``, cuantificable ->
        resta ``step`` sin bajar de ``0``); este metodo solo lo ejecuta y
        devuelve el resultado para que el llamador repinte la tecla.

        Args:
            habit: El habito sobre el que retroceder.

        Returns:
            El nuevo valor TOTAL acumulado hoy, o ``0.0`` si hoy no habia
            ningun progreso que deshacer (deshacer es seguro de repetir).

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """


class TaskProvider(ABC):
    """Puerto: contrato que debe implementar cualquier backend de tareas.

    Separado a proposito de ``HabitProvider``: son dos capacidades distintas y
    un adaptador puede ofrecer una sin la otra. Comparten, eso si, la misma
    jerarquia de excepciones ``Provider*``, de modo que ``core.health.classify``
    y el pintado de errores en tecla sirven igual para ambos.
    """

    @abstractmethod
    def get_tasks(self) -> list[Task]:
        """Devuelve las tareas pendientes que tocan hoy, ya ordenadas.

        El orden lo decide el proveedor (el llamador reparte las teclas en el
        orden en que las recibe), y la lista solo contiene tareas pendientes:
        una tarea cerrada simplemente deja de aparecer.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def complete_task(self, task: Task) -> None:
        """Marca ``task`` como completada.

        No devuelve nada: la tarea deja de estar pendiente, no pasa a un estado
        que el cliente deba pintar. Es idempotente, asi que reintentar es
        seguro. Que se vuelva a abrir el siguiente ciclo (tareas periodicas) lo
        decide el proveedor, no el llamador.

        Args:
            task: La tarea a cerrar.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """
