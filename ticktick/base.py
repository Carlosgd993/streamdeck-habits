"""Puerto abstracto para TickTick, deliberadamente independiente de
``provider/`` (que es la API de ``habits-core``): TickTick es un origen de
datos aparte, sin migracion ni escritura cruzada con esa base.

Reutiliza la jerarquia de excepciones ``Provider*Error`` de
``provider.base`` porque es agnostica de backend (no tiene nada especifico
de habits-core pese al nombre del modulo): asi ``core.health.classify`` y el
pintado de codigo de error en tecla funcionan igual aqui sin duplicar nada.
Tambien reutiliza ``provider.base.clip_title`` por la misma razon (utilidad
pura, sin dependencias de ningun proveedor concreto).

Solo hay un puerto (``TickTickProvider``), no cuatro como en ``provider/``:
esta PoC solo cubre tareas y proyectos, dos caras de la misma pantalla
"TickTick" del deck. Si en el futuro se anade otra capacidad de TickTick
(habitos, focus...), sera otro puerto separado aqui, mismo criterio que
separa ``HabitProvider`` de ``TaskProvider``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from provider.base import clip_title


class TickTickTask:
    """Tarea pendiente (o recien completada, ver ``completed``) de TickTick.

    A diferencia de ``provider.base.Task`` (que desaparece al completarse:
    una tarea de habits-core no tiene estado "hecha" que pintar), aqui
    ``completed`` SI es un estado visible: la pantalla "TickTick" del deck
    pinta una tarea completada en gris en vez de quitarla al instante, para
    poder deshacer una pulsacion por error -- solo deja de listarse en el
    proximo refresco real desde la API (ver ``core.screens.ScreenKind.TICKTICK``).

    Attributes:
        id: Identificador de la tarea en TickTick.
        project_id: Identificador del proyecto (lista) de TickTick al que
            pertenece. Necesario para completar/reabrir (la API lo pide en
            la ruta o en el cuerpo).
        title: Titulo de la tarea, ya sin el emoji si lo llevaba (ver
            ``ticktick.client.build_ticktick_task``).
        emoji: Emoji extraido del titulo, o cadena vacia. Mismo criterio que
            ``provider.base.Task.emoji``.
        priority: Prioridad de TickTick (``0``/``1``/``3``/``5``, mismos
            valores que ``provider.base.Task.priority`` por coincidencia del
            propio contrato de TickTick), decide el color de la tecla.
        completed: Si la tarea esta completada. Mutable: la pantalla del
            deck lo cambia de forma optimista al pulsar (ver
            ``orchestrator.press_ticktick_toggle``), antes de que llegue el
            proximo refresco real.
    """

    def __init__(
        self,
        id: str,
        project_id: str,
        title: str,
        emoji: str = "",
        priority: int = 0,
        completed: bool = False,
    ) -> None:
        self.id = id
        self.project_id = project_id
        self.title = title
        self.emoji = emoji
        self.priority = priority
        self.completed = completed

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el titulo, recortado si no cabe."""
        return clip_title(self.title)


class TickTickProject:
    """Un proyecto (lista) de TickTick, para la pantalla "TickTick" del deck:
    sus botones dan acceso a las tareas de un proyecto concreto (ver
    ``core.screens.ScreenKind.TICKTICK``).

    Attributes:
        id: Identificador del proyecto en TickTick. Es lo que lleva
            ``TickTickTask.project_id`` para saber a que proyecto pertenece
            una tarea.
        name: Nombre del proyecto.
        closed: Si el proyecto esta archivado en TickTick. Un proyecto
            cerrado no aparece como boton en el deck (ver
            ``core.screens.resolve_page``).
        sort_order: Orden propio de TickTick para este proyecto (mismo campo
            que usa su propia app), usado para ordenar los botones igual que
            en TickTick.
    """

    def __init__(self, id: str, name: str, closed: bool = False, sort_order: int = 0) -> None:
        self.id = id
        self.name = name
        self.closed = closed
        self.sort_order = sort_order

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el nombre, recortado si no cabe."""
        return clip_title(self.name)


class TickTickProvider(ABC):
    """Puerto: contrato que debe implementar un backend de tareas de TickTick.

    Tareas pendientes de todos los proyectos, Inbox incluido (ver
    ``get_tasks``), y la lista de proyectos para la pantalla "TickTick" del
    deck (ver ``get_projects``) -- nada de habitos ni focus de TickTick en
    esta PoC. ``ticktick.client.TickTickApiProvider`` expone ademas
    ``get_project_tasks`` (tareas de UN proyecto via la API), fuera de este
    puerto porque nada de lo que hay implementado hoy lo necesita: se
    incorporaria aqui el dia que algo lo use.
    """

    @abstractmethod
    def get_tasks(self) -> list[TickTickTask]:
        """Devuelve las tareas pendientes de todos los proyectos, Inbox
        incluido.

        Raises:
            ProviderAuthError: Si el token es invalido o caduco.
            ProviderNetworkError: Si falla la conexion con TickTick.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def get_projects(self) -> list[TickTickProject]:
        """Devuelve todos los proyectos de la cuenta (incluidos los
        cerrados: los filtra quien pinte la pantalla, no este puerto).

        Raises:
            ProviderAuthError: Si el token es invalido o caduco.
            ProviderNetworkError: Si falla la conexion con TickTick.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def complete_task(self, task: TickTickTask) -> None:
        """Marca ``task`` como completada.

        Args:
            task: La tarea a completar.

        Raises:
            ProviderAuthError: Si el token es invalido o caduco.
            ProviderNetworkError: Si falla la conexion con TickTick.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def uncomplete_task(self, task: TickTickTask) -> None:
        """Reabre ``task`` (deshace una completada por error).

        Validado contra la API real: reabrir con esto conserva titulo,
        prioridad y el resto de campos de la tarea intactos (no es un
        reemplazo completo).

        Args:
            task: La tarea a reabrir.

        Raises:
            ProviderAuthError: Si el token es invalido o caduco.
            ProviderNetworkError: Si falla la conexion con TickTick.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """
