"""Puertos abstractos de los proveedores de datos, agnosticos de la API concreta.

Este modulo define TODO lo que el resto del proyecto (orquestador, dominio,
deck) necesita saber sobre "la API", sin acoplarse a ningun backend en
particular:

- ``HabitProvider``: el puerto de habitos que cualquier backend debe implementar.
- ``TaskProvider``: el puerto de tareas, deliberadamente **separado** del
  anterior: un backend de habitos no tiene por que servir tambien tareas, y al
  reves.
- ``TemplateProvider``: el puerto de plantillas (crear una tarea a partir de una
  definicion reutilizable), separado de los otros dos por la misma razon.
  Un adaptador concreto puede implementar los tres (es lo que hace
  ``provider/supabase.py``, porque los tres salen del mismo contrato).
- Jerarquia de excepciones agnostica (``ProviderError`` y subclases), compartida
  por los tres puertos.
- Los modelos de dominio ``Habit`` (y sus subtipos ``BooleanHabit``/``RealHabit``/
  ``LogHabit``), ``Task`` y ``Template``, construidos desde campos ya parseados --
  nunca desde el JSON crudo de un backend.

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
        manual_entry: Si es ``True``, pulsar la tecla no avanza un paso: abre
            la pantalla de teclado numerico para fijar el valor exacto de hoy
            (``HabitProvider.set_value``). Solo tiene sentido en un habito
            cuantificable; vive en la clase base para que ``core.screens``
            pueda leerlo sin ``isinstance``, igual que ``goal``/``is_done``.
    """

    def __init__(
        self,
        id: str,
        name: str,
        emoji: str = "",
        order: int = 0,
        current_value: float = 0.0,
        manual_entry: bool = False,
    ) -> None:
        self.id = id
        self.name = name
        self.emoji = emoji
        self.order = order
        self.current_value = current_value
        self.manual_entry = manual_entry

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
        step: Cantidad que suma cada pulsacion (p.ej. ``1.0`` vaso). Sin uso
            si ``manual_entry`` es ``True``: ahi no se suma nada, se fija.
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
        manual_entry: bool = False,
    ) -> None:
        super().__init__(id, name, emoji, order, current_value, manual_entry)
        self._goal = goal
        self.step = step
        self.unit = unit

    @property
    def goal(self) -> float:
        """Objetivo diario del habito (p.ej. ``8.0`` vasos)."""
        return self._goal

    @property
    def progress_label(self) -> str:
        """Progreso de hoy sin unidad ni nombre (p.ej. ``"3/8"``, o ``"10/8"``
        por encima del objetivo). Solo el numero: lo usa ``display_label()``
        (que le añade la unidad) y la tecla informativa de
        ``core.screens.REAL_HABIT_OPTIONS_LAYOUT`` (que la deja tal cual,
        sin unidad -- la unidad va en su propia tecla ahi)."""
        return f"{_format_number(self.current_value)}/{_format_number(self.goal)}"

    def display_label(self) -> str:
        """Progreso acumulado hoy, sin el nombre (p.ej. ``"3/8 Cups"``).

        El nombre se omite a proposito: el emoji del habito ya identifica de
        que habito se trata, y en la tecla apenas hay sitio para nombre +
        progreso legibles. Puede superar el objetivo (p.ej. ``"10/8 Cups"``).

        Excepcion: si ``manual_entry`` es ``True`` (p.ej. "Peso") no hay
        "progreso hacia un objetivo" que mostrar -- el valor es una medicion,
        no un avance -- asi que aqui se comporta como ``BooleanHabit``: el
        nombre, sin numero. El valor sigue viendose en el teclado numerico al
        pulsar la tecla (ver ``core.screens``).
        """
        if self.manual_entry:
            return self.name
        progress = self.progress_label
        if self.unit:
            progress = f"{progress} {self.unit}"
        return progress


def _format_number(value: float) -> str:
    """Formatea un numero sin ``.0`` cuando es entero (``8`` en vez de ``8.0``)."""
    return str(int(value)) if value == int(value) else f"{value:g}"


class LogHabit(Habit):
    """Habito de solo registro (``purpose = 'log'`` en el backend): no tiene
    objetivo ni programacion, y nunca esta "pendiente" ni "hecho" -- es solo
    un boton para dejar constancia de que algo ocurrio (p.ej. "Desperte", con
    la hora exacta guardada por la base en ``habit_checkins.checkin_time``,
    que ni siquiera llega hasta aqui: ver ``provider.supabase.build_log_habit``).

    Pulsarlo llama a ``HabitProvider.step()``, la misma operacion y la misma
    RPC que un habito con objetivo -- el backend no distingue "avanzar" de
    "registrar", solo ``purpose`` decide si un habito puede aparecer
    "pendiente" en alguna vista. Por eso ``LogHabit`` vive en la misma
    jerarquia que ``BooleanHabit``/``RealHabit`` en vez de en un puerto propio:
    comparte la capacidad de "pulsar y avanzar", solo cambia que no se lee
    desde ``get_habits()`` (que nunca la devuelve, ver ``HabitProvider``) ni
    se pinta con el mismo criterio de color (ver ``deck.renderer.render_habit``).

    Attributes:
        color: Color propio del habito en formato ``"#RRGGBB"``, o cadena
            vacia si no esta definido en la base. Es la unica senal visual
            propia que trae el contrato para un log: al no haber estado
            pendiente/hecho que pintar en blanco/gris, el color es lo que
            distingue un log de otro de un vistazo. Un habito con objetivo
            tambien tiene esta columna en la base, pero el cliente la ignora
            a proposito porque ya usa el eje blanco/gris.
        cumulative: Si ``True`` (``type == "Real"`` en la base), varias
            pulsaciones el mismo dia se acumulan (``habit_step`` suma
            ``step`` sin tope, igual que un ``RealHabit`` con objetivo) y la
            tecla enseña un contador (p.ej. "Cocacola x3"). Si ``False``
            (``type == "Boolean"``, el caso de "Desperte"), es un registro
            unico diario: pulsarlo de nuevo no acumula nada (``habit_step``
            vuelve a fijar el mismo valor), asi que no tiene sentido mostrar
            numero.
        unit: Unidad del log (p.ej. ``"Cups"``), vacia si no esta definida.
            Solo se usa si ``cumulative`` es ``True`` y hay progreso hoy.
    """

    def __init__(
        self,
        id: str,
        name: str,
        emoji: str = "",
        order: int = 0,
        current_value: float = 0.0,
        color: str = "",
        cumulative: bool = False,
        unit: str = "",
    ) -> None:
        super().__init__(id, name, emoji, order, current_value)
        self.color = color
        self.cumulative = cumulative
        self.unit = unit

    def display_label(self) -> str:
        """El nombre del habito, y si es acumulable y hoy ya tiene registros,
        el contador de veces (p.ej. "Cocacola x3"). Un log no acumulable
        (p.ej. "Desperte") es un registro unico diario: pulsar la tecla ya es
        el dato completo, nunca enseña numero."""
        if not self.cumulative or self.current_value <= 0:
            return self.name
        count = f"x{_format_number(self.current_value)}"
        if self.unit:
            count = f"{count} {self.unit}"
        return f"{self.name} {count}"


TITLE_MAX_CHARS = 32
"""Longitud maxima del titulo de una tarea o plantilla en una tecla.

A partir de ahi el texto ya no cabe (el pintado envuelve en lineas cortas) y lo
que sobra se recorta con una elipsis en vez de desbordarse en silencio."""


def _clip_title(title: str) -> str:
    """Recorta ``title`` a ``TITLE_MAX_CHARS`` con elipsis si no cabe en la tecla."""
    if len(title) <= TITLE_MAX_CHARS:
        return title
    return title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


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
        template_id: Id de la ``Template`` de la que salio esta ocurrencia, o
            cadena vacia si es una tarea unica. Es lo que permite saber si una
            plantilla ya tiene una ocurrencia pendiente sin preguntar otra vez
            al proveedor (ver ``core.screens``).
    """

    def __init__(
        self,
        id: str,
        title: str,
        emoji: str = "",
        priority: int = 0,
        overdue: bool = False,
        due_day: str = "",
        template_id: str = "",
    ) -> None:
        self.id = id
        self.title = title
        self.emoji = emoji
        self.priority = priority
        self.overdue = overdue
        self.due_day = due_day
        self.template_id = template_id

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el titulo, recortado si no cabe.

        El emoji va aparte (ver ``emoji``), como en los habitos.
        """
        return _clip_title(self.title)


class Template:
    """Plantilla de tarea de creacion rapida, agnostica del backend.

    Es la definicion reutilizable de una tarea que se repite **sin momento
    fijo** ("Cita peluquero"): no la materializa nadie automaticamente, la crea
    el usuario cuando le toca. Solo llegan aqui las plantillas que el proveedor
    marca como de creacion rapida; el resto (las que se materializan solas) no
    se pintan nunca.

    Al contrario que ``Task``, una plantilla **no desaparece** al usarla: sigue
    ahi para la proxima vez.

    Attributes:
        id: Identificador de la plantilla en el proveedor. Es el que se envia
            para crear una ocurrencia -- ojo, no es el id de la tarea creada.
        title: Titulo legible, ya sin el emoji si lo llevaba.
        emoji: Emoji del titulo para usar como icono, o cadena vacia. Mismo
            criterio que en ``Task``: no hay campo de icono propio, se saca del
            titulo.
        priority: Prioridad que heredara la ocurrencia creada (``0``/``1``/``3``/
            ``5``). Se guarda por coherencia con ``Task``, aunque el pintado de
            una plantilla no dependa de ella.
        has_pending: Si ya existe una ocurrencia pendiente de esta plantilla.
            **Lo calcula el dominio en cada resolucion de pantalla** (no viene
            del backend), cruzando las plantillas con las tareas pendientes. Es
            lo que evita crear un duplicado por accidente: la tecla se pinta en
            gris y la pulsacion no hace nada.
    """

    def __init__(
        self,
        id: str,
        title: str,
        emoji: str = "",
        priority: int = 0,
        has_pending: bool = False,
    ) -> None:
        self.id = id
        self.title = title
        self.emoji = emoji
        self.priority = priority
        self.has_pending = has_pending

    def display_label(self) -> str:
        """Texto a mostrar en la tecla: el titulo, recortado si no cabe.

        El emoji va aparte (ver ``emoji``), igual que en habitos y tareas.
        """
        return _clip_title(self.title)


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
    def get_log_habits(self) -> list[Habit]:
        """Devuelve los habitos de solo registro (``LogHabit``), sin progreso
        que interpretar: al reves que ``get_habits()``, aqui no hay
        "pendiente" ni "hecho" -- la lista sale entera siempre, no se filtra
        por nada. Metodo separado de ``get_habits()`` (en vez de un
        discriminador dentro de la misma lista) para que esa otra interfaz
        no cambie: todo lo que ya consume ``get_habits()`` (paginacion
        estable de "Habitos", filtro de pendientes en "Hoy") sigue viendo
        exactamente los habitos con objetivo de siempre.

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

    @abstractmethod
    def set_value(self, habit: Habit, value: float) -> float:
        """Fija el valor exacto de hoy de ``habit`` (entrada manual).

        A diferencia de ``step``/``undo``, aqui el llamador decide el valor:
        es la operacion que usa un habito ``manual_entry`` (p.ej. "Peso") tras
        teclearlo en el deck. El proveedor sigue teniendo la ultima palabra
        (no baja de 0), pero no suma ni resta nada relativo al valor previo.

        Args:
            habit: El habito sobre el que fijar el valor.
            value: El valor exacto a registrar hoy.

        Returns:
            El nuevo valor TOTAL acumulado hoy.

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

    @abstractmethod
    def skip_task(self, task: Task) -> None:
        """Marca ``task`` como omitida (no se hizo hoy).

        No devuelve nada, igual que ``complete_task``: la tarea deja de
        estar pendiente (sale de ``get_tasks()``), pero el rastro queda
        como "omitida" en vez de "hecha". Es idempotente en la base. La usa
        el menu de opciones de una tarea (mantener pulsado, ver
        ``core.screens``).

        Args:
            task: La tarea a omitir.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def set_priority(self, task: Task, priority: int) -> None:
        """Cambia la prioridad de ``task`` a ``priority``.

        No devuelve nada, igual que ``complete_task``: la tarea sigue
        pendiente, solo cambia su color en el deck. La usa el menu de
        opciones de una tarea (mantener pulsado, ver ``core.screens``).

        Args:
            task: La tarea sobre la que cambiar la prioridad.
            priority: Nueva prioridad (``0``/``1``/``3``/``5``).

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado
                (incluida una prioridad fuera de ``0``/``1``/``3``/``5``).
        """


class TemplateProvider(ABC):
    """Puerto: contrato que debe implementar un backend de plantillas de tarea.

    Separado de ``TaskProvider`` por la misma razon que este lo esta de
    ``HabitProvider``: crear tareas a demanda desde una definicion reutilizable
    es una capacidad distinta de listarlas y cerrarlas, y un backend puede
    ofrecer una sin la otra. Comparte la jerarquia de excepciones ``Provider*``.
    """

    @abstractmethod
    def get_templates(self) -> list[Template]:
        """Devuelve las plantillas de creacion rapida, ya ordenadas.

        Solo las que el proveedor marca como tales: las plantillas que se
        materializan solas no se ofrecen como boton. El filtrado y el orden los
        decide el proveedor, igual que en ``get_tasks``.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """

    @abstractmethod
    def create_task(self, template: Template) -> str:
        """Crea una ocurrencia de tarea a partir de ``template``.

        El proveedor decide todo lo de la tarea nueva (titulo, prioridad,
        subtareas y **la fecha de vencimiento**): este metodo no envia ninguna
        fecha, igual que ``step`` no envia ningun valor.

        **No es idempotente**, al contrario que ``complete_task``: cada llamada
        crea una ocurrencia mas. Reintentar a ciegas duplica, asi que el
        llamador debe mirar ``Template.has_pending`` antes de invocarlo.

        Args:
            template: La plantilla de la que crear la ocurrencia.

        Returns:
            El id de la ocurrencia recien creada.

        Raises:
            ProviderAuthError: Si las credenciales son invalidas o caducaron.
            ProviderNetworkError: Si falla la conexion con el proveedor.
            ProviderDataError: Si la respuesta no tiene el formato esperado.
        """
