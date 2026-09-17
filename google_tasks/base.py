"""Puerto abstracto para Google Tasks, deliberadamente independiente de
``provider/`` (que es la API de ``habits-core``): Google Tasks es un origen de
datos aparte, sin migracion ni escritura cruzada con esa base -- mismo patron
que ``ticktick/`` (ver ese modulo, la primera pantalla de este tipo, para el
precedente).

Reutiliza la jerarquia de excepciones ``Provider*Error`` de ``provider.base``
porque es agnostica de backend, y ``provider.base.clip_title`` por la misma
razon (utilidad pura, sin dependencias de ningun proveedor concreto) -- mismo
criterio que ``ticktick/base.py``.

Solo hay un puerto (``GoogleTasksProvider``): esta PoC solo cubre listas y
tareas de Google Tasks, dos caras de la misma pantalla "Google Tasks" del
deck.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from provider.base import clip_title


class GoogleTask:
    """Tarea pendiente (o recien completada, ver ``completed``) de Google Tasks.

    A diferencia de ``provider.base.Task`` (que desaparece al completarse: una
    tarea de habits-core no tiene estado "hecha" que pintar), aqui
    ``completed`` SI es un estado visible -- mismo criterio que
    ``ticktick.base.TickTickTask.completed``: la pantalla "Google Tasks" del
    deck pinta una tarea completada en gris en vez de quitarla al instante,
    para poder deshacer una pulsacion por error -- solo deja de listarse en el
    proximo refresco real desde la API (ver
    ``core.screens.ScreenKind.GOOGLE_TASKS``).

    Attributes:
        id: Identificador de la tarea en Google Tasks.
        list_id: Identificador de la lista (tasklist) a la que pertenece.
            Necesario para completar/reabrir (la API lo pide en la ruta) --
            el propio recurso ``Task`` de Google NO trae este campo (a
            diferencia de ``projectId`` en TickTick), asi que
            ``google_tasks.client.build_google_task`` lo recibe explicito de
            quien ya sabe de que lista viene (ver ``GoogleTasksApiProvider.
            get_tasks``).
        title: Titulo de la tarea, ya sin el emoji si lo llevaba (ver
            ``google_tasks.client.build_google_task``).
        emoji: Emoji extraido del titulo, o cadena vacia. Mismo criterio que
            ``ticktick.base.TickTickTask.emoji``.
        due: Fecha de vencimiento (RFC 3339, solo fecha -- Google descarta la
            hora al fijarla), o cadena vacia si no tiene. No la usa el
            pintado (ver el aviso de prioridad, mas abajo): se conserva por
            si una vista futura quiere ordenar u ofrecer un aviso por
            vencimiento, igual que ``parent``/``position``.
        parent: Id de la tarea padre si esta es una subtarea, o cadena vacia
            si es de primer nivel (campo *output only* del contrato). Hoy no
            se agrupa visualmente: una subtarea se pinta como una tarea mas,
            plana -- ocultarla esconderia trabajo real. Se conserva por si
            una vista futura quiere agruparlas.
        position: Orden manual que el usuario le dio en la propia app de
            Google Tasks (campo *output only*, cadena opaca que SI ordena
            correctamente como texto dentro de una misma lista -- ver
            ``core.screens._google_tasks_sort_key``). Es el criterio de orden
            que usa la pantalla "Google Tasks" del deck, para que las teclas
            aparezcan en el mismo orden que ve el usuario en su propia app.
        completed: Si la tarea esta completada. Mutable: la pantalla del
            deck lo cambia de forma optimista al pulsar (ver
            ``orchestrator.press_google_task_toggle``), antes de que llegue
            el proximo refresco real.

    A diferencia de TickTick (que expone ``priority`` con la misma escala
    ``0``/``1``/``3``/``5`` de ``provider.base.Task`` por coincidencia de su
    propio contrato), el contrato de Google Tasks **no tiene campo de
    prioridad**: no se inventa ninguna derivandola de otra cosa (p.ej. de
    ``due``) -- toda tarea se pinta con el color liso de la prioridad 0
    (blanca), salvo completada (gris) -- ver
    ``deck.renderer.render_google_task``.
    """

    def __init__(
        self,
        id: str,
        list_id: str,
        title: str,
        emoji: str = "",
        due: str = "",
        parent: str = "",
        position: str = "",
        completed: bool = False,
    ) -> None:
        self.id = id
        self.list_id = list_id
        self.title = title
        self.emoji = emoji
        self.due = due
        self.parent = parent
        self.position = position
        self.completed = completed

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el titulo, recortado si no cabe."""
        return clip_title(self.title)


class GoogleTaskList:
    """Una lista de tareas (tasklist) de Google Tasks, para la pantalla
    "Google Tasks" del deck: sus botones dan acceso a las tareas de una lista
    concreta -- mismo papel que ``ticktick.base.TickTickProject``.

    A diferencia de un proyecto de TickTick (que puede archivarse, ver
    ``TickTickProject.closed``), el contrato de ``tasklists`` de Google Tasks
    no tiene un equivalente: todas las listas de la cuenta aparecen siempre
    como boton en la pantalla principal, no hay ninguna que filtrar.

    Attributes:
        id: Identificador de la lista en Google Tasks. Es lo que lleva
            ``GoogleTask.list_id`` para saber a que lista pertenece una tarea.
        title: Nombre de la lista.
    """

    def __init__(self, id: str, title: str) -> None:
        self.id = id
        self.title = title

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el nombre, recortado si no cabe."""
        return clip_title(self.title)


class GoogleTasksProvider(ABC):
    """Puerto: contrato que debe implementar un backend de Google Tasks.

    A diferencia de ``TickTickProvider.get_tasks`` (una sola peticion trae
    TODAS las tareas de golpe, de cualquier proyecto), la API de Google Tasks
    no tiene un endpoint global: hay que pedir las listas y luego las tareas
    de cada una, una peticion por lista (ver ``get_tasks``). Por eso este
    puerto recibe los ids de lista como argumento explicito en vez de no
    llevar ninguno: deja ese "1+N" a la vista en la firma, y evita que el
    adaptador tenga que volver a pedir las listas el solo para saber cuales
    pedir.
    """

    @abstractmethod
    def get_task_lists(self) -> list[GoogleTaskList]:
        """Devuelve todas las listas (tasklists) de la cuenta.

        Raises:
            ProviderAuthError: Si el token es invalido, caducado o revocado.
            ProviderNetworkError: Si falla la conexion con Google.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def get_tasks(self, list_ids: Sequence[str]) -> list[GoogleTask]:
        """Devuelve las tareas pendientes de las listas en ``list_ids``.

        Una peticion por lista (no hay equivalente al ``/task/filter`` de
        TickTick en la API de Google Tasks, ver el docstring de esta clase).

        Args:
            list_ids: Ids de las listas cuyas tareas se quieren, tipicamente
                todas las que devolvio ``get_task_lists()``.

        Raises:
            ProviderAuthError: Si el token es invalido, caducado o revocado.
            ProviderNetworkError: Si falla la conexion con Google.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def complete_task(self, task: GoogleTask) -> None:
        """Marca ``task`` como completada.

        Args:
            task: La tarea a completar.

        Raises:
            ProviderAuthError: Si el token es invalido, caducado o revocado.
            ProviderNetworkError: Si falla la conexion con Google.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def uncomplete_task(self, task: GoogleTask) -> None:
        """Reabre ``task`` (deshace una completada por error).

        A diferencia del ``uncomplete_task`` de TickTick (un Update no
        documentado, validado a mano contra la API real), este SI esta
        documentado en la API oficial de Google Tasks: fijar
        ``status: "needsAction"`` es la operacion simetrica e inversa de
        completar, sin necesitar ninguna comprobacion previa.

        Args:
            task: La tarea a reabrir.

        Raises:
            ProviderAuthError: Si el token es invalido, caducado o revocado.
            ProviderNetworkError: Si falla la conexion con Google.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """
