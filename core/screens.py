"""Registro extensible de pantallas: menu principal, submenu "Sistema" y las
vistas de datos (Hoy/Habitos/Tareas/Crear...), todas resueltas con la misma
pareja de funciones puras.

Una pantalla resuelta en su pagina actual reparte las 15 teclas en varios
cubos -- habito, tarea, plantilla, etiqueta de cronometro, entrada de menu o
boton del teclado numerico -- mas un total de paginas. Menu y Sistema son listas fijas de
``MenuEntry`` paginadas con ``core.key_map.paginate``; cada vista de datos se
resuelve con la funcion ``build_page`` que registra su ``ViewSpec`` en
``VIEWS``. Anadir una vista nueva es registrar una entrada mas en ``VIEWS``
(con un ``_tiered_page_builder``/``_flat_page_builder``, o uno propio) y un
boton mas en ``MENU_ENTRIES``; nada mas del sistema cambia.

Hay tres pantallas que no son menu/sistema/vista y no aparecen en ``VIEWS``:
el teclado numerico (``ScreenKind.NUMERIC_ENTRY``, ver ``NUMERIC_KEYPAD``),
que abre un habito ``manual_entry`` al pulsarlo; el menu de opciones de un
habito/tarea (``ScreenKind.ITEM_OPTIONS``, ver ``HABIT_OPTIONS_LAYOUT``/
``REAL_HABIT_OPTIONS_LAYOUT``/``TASK_OPTIONS_LAYOUT`` -- un habito elige entre
los dos primeros segun su tipo), que abre mantener pulsado un habito o una
tarea (la duracion la mide ``orchestrator.make_key_callback``, este modulo
solo describe la pantalla); y el stand by (``ScreenKind.STANDBY``), en el que
el deck esta apagado y **cualquier** tecla se limita a despertarlo.

Las secciones de habitos (``habit_sections``/``habits.section_id`` en
``../habits-core``) son un caso aparte, ni vista registrada ni pantalla
nueva: ``ScreenState.section_name`` es un campo mas de ``ScreenKind.VIEW``
(no un ``ScreenKind`` propio, para que mantener pulsado un habito de una
seccion y "Volver" sigan funcionando sin cambios), resuelto por
``_section_page`` en vez de por ``VIEWS``. Se llega a traves de
``KEY_HABITS_SECTIONS_SHORTCUT`` (tecla 1 de "Habitos"), que abre el submenu
"Secciones" (``ScreenKind.SECTIONS_MENU``, ``_section_menu_entries``) --
cualquier seccion aparece ahi sola en cuanto algun habito la tenga asignada,
sin tocar nada de este modulo. Mantener pulsada una entrada de "Secciones"
abre ``ScreenKind.SECTION_OPTIONS``, con un boton para fijarla/quitarla del
menu principal (``_pinned_section_menu_entries``, ``core.pinned_sections`` --
una preferencia local del deck, no un dato de ``../habits-core``): es la
UNICA forma de que una seccion tenga entrada en el menu principal, deliberado
para no duplicar el acceso por defecto.

Los proyectos de tarea (``projects``/``tasks.project_id`` en
``../habits-core``) siguen exactamente el mismo patron que las secciones,
pero sobre ``Task``/"Tareas" en vez de ``Habit``/"Habitos":
``ScreenState.project_name``, ``_project_page``,
``KEY_TASKS_PROJECTS_SHORTCUT`` (tecla 1 de "Tareas"),
``ScreenKind.PROJECTS_MENU``/``_project_menu_entries``,
``ScreenKind.PROJECT_OPTIONS``/``_pinned_project_menu_entries``/
``core.pinned_projects``. A diferencia de las secciones, ``project_name`` no
estaba resuelto en ``v_today_tasks`` de fabrica: hizo falta una migration en
``../habits-core`` que anadiera el ``left join`` a ``projects``, mismo patron
que ``section_name`` en ``v_today_habits``.

"TickTick" (``ScreenKind.TICKTICK``) es un caso distinto de todos los
anteriores: no es un filtro sobre datos de habits-core, es una pantalla
completa con su propio origen de datos (``ticktick/``, PoC deliberadamente
independiente de habits-core -- ver ``ticktick/base.py``). Vive fuera del
mecanismo de ``VIEWS``/``PageBuilder`` por el mismo motivo que
``NUMERIC_ENTRY``/``ITEM_OPTIONS``/``STANDBY``: su refresco tiene que ir
desacoplado del ciclo de habits-core (``orchestrator.ticktick_refresh_cycle``,
aparte de ``refresh_cycle``), asi que forzarla a compartir la firma de 8
conjuntos de datos que reciben las vistas de ``VIEWS`` no aportaria nada. La
tecla 5/10 se comportan normal (paginacion); la 0 solo se reinterpreta
filtrada a un proyecto (ver justo abajo), en la principal sigue abriendo el
menu como siempre.

La pantalla principal de "TickTick" (``ScreenState.ticktick_project_id``
vacio) mezcla dos cosas en una sola lista paginada: un boton por cada
proyecto de TickTick (azul de navegacion, reutilizando ``MenuEntry``/
``ResolvedPage.key_nav``/``deck.renderer.render_nav_entry`` -- entrar en uno
es tan solo navegar, igual que "enter_section"/"enter_project") seguido de
las tareas sin proyecto reconocido (Inbox, y cualquier tarea de un proyecto
``closed`` -- ver ``TickTickProject.closed``), estas si en
``ResolvedPage.key_ticktick``. Pulsar un boton de proyecto entra en esa
misma pantalla con ``ticktick_project_id`` puesto (mismo patron que
``section_name``/``project_name`` en ``ScreenKind.VIEW``: un campo mas de la
pantalla, no un ``ScreenKind`` propio), que filtra a solo las tareas de ese
proyecto -- aqui la tecla 0 SI se reinterpreta, como "Volver" a la pantalla
principal (``PressAction("exit_ticktick_project")``, reutilizando el mismo
``OptionEntry``/``render_option_entry`` que cualquier otro "Volver" del
deck) en vez de abrir el menu principal. "TickTick" desde el menu siempre
entra en la principal, nunca conserva el filtro (ver
``orchestrator._enter_ticktick``).

Este modulo no sabe nada del Stream Deck (no importa nada de ``deck/``): solo
depende de ``config`` (constantes de teclas/paginacion), ``core.key_map``
(``paginate``), ``provider.base`` (``Habit``/``RealHabit``/``Task``/
``Template``/``TimerLabel``/``RunningTimer``) y ``ticktick.base``
(``TickTickTask``), igual que el resto de ``core/``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from config import ALL_KEYS, AVAILABLE_KEYS, KEY_MENU, KEY_PAGE_NEXT, KEY_PAGE_PREV, PAGE_SIZE
from core.key_map import paginate
from provider.base import BooleanHabit, Habit, RealHabit, RunningTimer, Task, Template, TimerLabel, clip_title
from ticktick.base import TickTickProject, TickTickTask


class ScreenKind(Enum):
    """De que tipo es la pantalla activa."""

    MENU = auto()
    SYSTEM = auto()
    VIEW = auto()
    NUMERIC_ENTRY = auto()
    STANDBY = auto()
    ITEM_OPTIONS = auto()
    SECTIONS_MENU = auto()
    SECTION_OPTIONS = auto()
    PROJECTS_MENU = auto()
    PROJECT_OPTIONS = auto()
    TICKTICK = auto()


@dataclass
class ScreenState:
    """Pantalla activa del deck, mutable y solo en memoria.

    No se persiste nunca: cada arranque del daemon empieza en la vista por
    defecto (``DEFAULT_VIEW_ID``, "Hoy"), pagina 0.

    Attributes:
        kind: Menu principal, submenu Sistema, una vista de datos, el teclado
            numerico de entrada manual, el menu de opciones de un
            habito/tarea (mantener pulsado) o el stand by (pantalla apagada).
        view_id: Id de la vista activa en ``VIEWS``. Solo tiene sentido si
            ``kind`` es ``ScreenKind.VIEW``.
        page: Pagina 0-indexada dentro de la pantalla activa.
        entry_habit_id: Id del habito que se esta editando, solo si ``kind``
            es ``ScreenKind.NUMERIC_ENTRY``. Al entrar al teclado no se tocan
            ``view_id``/``page``: es lo que permite que "Salir" vuelva
            exactamente a la vista/pagina de origen sin un campo aparte.
        entry_value: Lo tecleado hasta ahora en el teclado numerico (cadena,
            no numero, para poder representar estados intermedios como
            ``"12."``). Vacio fuera de ``NUMERIC_ENTRY``.
        entry_item_kind: "habit" o "task", solo si ``kind`` es
            ``ScreenKind.ITEM_OPTIONS``: de que tipo es el elemento sobre el
            que se abrio el menu de opciones (una tarea no tiene "deshacer"
            ni objetivo, un habito si, asi que una opcion real necesitara
            saber cual es de los dos).
        entry_item_id: Id del habito/tarea sobre el que se abrio el menu de
            opciones. Igual que ``entry_habit_id``, entrar aqui no toca
            ``view_id``/``page``: "Volver" regresa exactamente a la vista de
            origen sin un campo aparte.
        section_name: Nombre de la seccion activa, solo si ``kind`` es
            ``ScreenKind.VIEW`` y no esta vacio -- en ese caso ``resolve_page``/
            ``_undoes`` resuelven contra la seccion (``_section_page``) en vez
            de contra ``VIEWS[view_id]``, y ``view_id`` queda sin consultar
            mientras tanto. Vive como un campo mas de ``ScreenKind.VIEW`` (no
            como un ``ScreenKind`` propio) a proposito: asi mantener pulsado un
            habito de una seccion y pulsar "Volver" en su menu de opciones
            restaura la seccion solo, con el mismo mecanismo que ya usa
            cualquier otra vista (``_exit_item_options``/``_exit_numeric_entry``
            hacen ``screen.kind = ScreenKind.VIEW`` sin tocar nada mas).
        entry_section_name: Nombre de la seccion sobre la que se abrio su
            pantalla de opciones, solo si ``kind`` es
            ``ScreenKind.SECTION_OPTIONS``. Campo propio, no compartido con
            ``entry_item_id``: a diferencia de ``ITEM_OPTIONS`` (que siempre
            vuelve a ``ScreenKind.VIEW``, ver ``_exit_item_options``), "Volver"
            aqui tiene que volver a la pantalla de origen -- mezclar el campo
            arriesgaria mezclar tambien el destino. Entrar aqui no toca
            ``screen.page``: la pagina de la pantalla de origen (la lista
            "Secciones" o el menu principal) queda intacta de propina.
        entry_section_origin: Desde que pantalla se abrio
            ``ScreenKind.SECTION_OPTIONS`` -- ``ScreenKind.SECTIONS_MENU``
            (mantener pulsada una entrada de "Secciones") o
            ``ScreenKind.MENU`` (mantener pulsado un boton de seccion ya
            fijado en el menu principal, ver ``core.pinned_sections``). Solo
            tiene sentido si ``kind`` es ``ScreenKind.SECTION_OPTIONS``:
            ``orchestrator._exit_section_options`` vuelve exactamente aqui al
            pulsar "Volver", en vez de asumir siempre "Secciones" -- las dos
            pantallas pueden llevar a la misma sección, y cada una espera
            volver a si misma.
        project_name: Nombre del proyecto activo, solo si ``kind`` es
            ``ScreenKind.VIEW`` y no esta vacio -- mismo mecanismo que
            ``section_name`` (campo mas de ``ScreenKind.VIEW``, no un
            ``ScreenKind`` propio) pero para tareas: ``resolve_page`` resuelve
            contra ``_project_page`` en vez de ``VIEWS[view_id]`` mientras
            este relleno, y ``view_id`` queda sin consultar. ``section_name``
            y ``project_name`` nunca estan rellenos los dos a la vez.
        entry_project_name: Nombre del proyecto sobre el que se abrio su
            pantalla de opciones, solo si ``kind`` es
            ``ScreenKind.PROJECT_OPTIONS``. Mismo papel que
            ``entry_section_name`` para secciones -- campo propio, no
            compartido con ``entry_item_id``/``entry_section_name``.
        entry_project_origin: Desde que pantalla se abrio
            ``ScreenKind.PROJECT_OPTIONS`` -- ``ScreenKind.PROJECTS_MENU`` o
            ``ScreenKind.MENU`` (boton de proyecto ya fijado). Mismo papel
            que ``entry_section_origin``: ``orchestrator._exit_project_options``
            vuelve exactamente aqui al pulsar "Volver".
        ticktick_project_id: Id del proyecto de TickTick activo, solo si
            ``kind`` es ``ScreenKind.TICKTICK`` y no esta vacio -- mismo
            mecanismo que ``section_name``/``project_name`` (un campo mas de
            la pantalla, no un ``ScreenKind`` propio): mientras este relleno,
            ``resolve_page`` filtra a solo las tareas de ese proyecto en vez
            de pintar la pantalla principal (botones de proyecto + tareas sin
            proyecto). Vacio de fabrica y tambien cada vez que se entra en
            "TickTick" desde el menu (``orchestrator._enter_ticktick``): no
            hay boton de "volver" a la principal, asi que sin este reseteo
            reabrir "TickTick" se quedaria en el ultimo proyecto visitado.
    """

    kind: ScreenKind = ScreenKind.VIEW
    view_id: str = "today"
    page: int = 0
    entry_habit_id: str = ""
    entry_value: str = ""
    entry_item_kind: str = ""
    entry_item_id: str = ""
    section_name: str = ""
    entry_section_name: str = ""
    entry_section_origin: ScreenKind = ScreenKind.SECTIONS_MENU
    project_name: str = ""
    entry_project_name: str = ""
    entry_project_origin: ScreenKind = ScreenKind.PROJECTS_MENU
    ticktick_project_id: str = ""


@dataclass(frozen=True)
class ViewItem:
    """Un habito, una tarea, una plantilla o una etiqueta de cronometro,
    envuelto para poder mezclarlos en una sola lista paginable (p.ej. el
    sobrante que no cupo en el mapeo estable, o la lista de "Hoy" que alterna
    habitos y tareas)."""

    kind: str  # "habit" | "task" | "template" | "timer_label"
    obj: Habit | Task | Template | TimerLabel


@dataclass(frozen=True)
class MenuEntry:
    """Un boton de menu o de submenu.

    Attributes:
        label: Texto del boton.
        emoji: Icono a color, o cadena vacia.
        action: Que hace al pulsarlo -- "select_view" (entra en ``view_id``),
            "open_system" (abre el submenu Sistema), "open_sections" (abre el
            submenu "Secciones", ver ``ScreenKind.SECTIONS_MENU``),
            "enter_section" (entra en la seccion ``section_name``),
            "open_projects" (abre el submenu "Proyectos", ver
            ``ScreenKind.PROJECTS_MENU``), "enter_project" (entra en el
            proyecto ``project_name``), "open_ticktick" (entra en la
            pantalla "TickTick", ver ``ScreenKind.TICKTICK``),
            "enter_ticktick_project" (filtra esa misma pantalla al proyecto
            de TickTick ``ticktick_project_id``), "standby" (apaga la
            pantalla del deck) o "shutdown" (apaga la Raspberry Pi). No hay
            boton de "volver": la tecla de menu ya vuelve al menu principal
            desde cualquier pantalla, incluida Sistema, asi que un boton
            "Atras" seria redundante.
        view_id: Id de la vista a la que lleva, solo si ``action`` es
            "select_view".
        key: Tecla fija dentro de la pagina 0 (p.ej. "Sistema" siempre en la
            14), o ``None`` para repartirse automaticamente entre las que
            sobren, en el orden en que aparece en la lista.
        section_name: Nombre de la seccion a la que lleva, solo si ``action``
            es "enter_section" -- usado tanto por el atajo fijo de la tecla 1
            de "Habitos" (ver ``KEY_HABITS_SECTIONS_SHORTCUT``/``resolve_page``)
            como por las entradas que genera ``_section_menu_entries`` para el
            submenu "Secciones".
        project_name: Nombre del proyecto al que lleva, solo si ``action`` es
            "enter_project" -- mismo papel que ``section_name`` pero para
            proyectos (tecla 1 de "Tareas"/``KEY_TASKS_PROJECTS_SHORTCUT`` y
            ``_project_menu_entries`` para el submenu "Proyectos").
        ticktick_project_id: Id del proyecto de TickTick al que lleva, solo
            si ``action`` es "enter_ticktick_project" -- mismo papel que
            ``project_name``, pero con el id real de TickTick (``TickTickProject.id``)
            en vez de un nombre: a diferencia de un proyecto de habits-core,
            aqui si hay un id estable que usar. Lo generan los botones de la
            pantalla principal de "TickTick" (ver ``resolve_page``).
    """

    label: str
    emoji: str
    action: str
    view_id: str = ""
    key: int | None = None
    section_name: str = ""
    project_name: str = ""
    ticktick_project_id: str = ""


@dataclass(frozen=True)
class StandbyKey:
    """Una tecla con contenido en la pantalla de stand by (ver ``STANDBY_LAYOUT``).

    Attributes:
        label: Texto de la tecla, o cadena vacia para dejar solo el icono.
        emoji: Icono a color, o cadena vacia.
    """

    label: str
    emoji: str


# Que se ve mientras el deck esta suspendido. **Este dict es el unico sitio que
# hay que tocar para cambiarlo**: las teclas que no aparecen aqui se pintan
# vacias (negras), asi que anadir, quitar o mover contenido es editar entradas.
# Por defecto solo la tecla central lleva un icono, lo justo para distinguir
# "suspendida" de "apagada" sin encender apenas nada.
#
# Se pinta de verdad (no basta con dejar lo que hubiera): el brillo de stand by
# no es 0, asi que la pantalla anterior se seguiria intuyendo.
STANDBY_LAYOUT: dict[int, StandbyKey] = {
    7: StandbyKey("", "🌙"),
}

_STANDBY_BLANK = StandbyKey("", "")  # relleno de las teclas sin contenido en stand by


@dataclass(frozen=True)
class NumericKey:
    """Un boton del teclado numerico de entrada manual (ver ``NUMERIC_KEYPAD``).

    Attributes:
        kind: Que hace -- "digit" (teclea ``label``), "decimal" (anade "."),
            "backspace" (borra el ultimo caracter), "confirm" (fija el valor
            tecleado), "cancel" (vuelve a la vista de origen sin enviar nada)
            o "display" (no es un boton: solo muestra lo tecleado hasta
            ahora, ``resolve_press`` la trata como "noop").
        label: Texto fijo del boton (el digito, ".", "OK", "Salir", "Borrar"),
            salvo en "display", donde ``resolve_page`` lo sustituye por
            ``ScreenState.entry_value`` en cada resolucion.
    """

    kind: str
    label: str


# Layout fijo de la pantalla de entrada manual (ver "Menu y pantallas" en
# CLAUDE.md). Las teclas 0/5/10 se reinterpretan aqui: no son menu/paginacion,
# son "salir"/"borrar"/"confirmar".
NUMERIC_KEYPAD: dict[int, NumericKey] = {
    0: NumericKey("cancel", "Salir"),
    1: NumericKey("display", ""),
    2: NumericKey("digit", "1"),
    3: NumericKey("digit", "2"),
    4: NumericKey("digit", "3"),
    5: NumericKey("backspace", "Borrar"),
    6: NumericKey("digit", "0"),
    7: NumericKey("digit", "4"),
    8: NumericKey("digit", "5"),
    9: NumericKey("digit", "6"),
    10: NumericKey("confirm", "OK"),
    11: NumericKey("decimal", "."),
    12: NumericKey("digit", "7"),
    13: NumericKey("digit", "8"),
    14: NumericKey("digit", "9"),
}


@dataclass(frozen=True)
class OptionEntry:
    """Una tecla de la pantalla de opciones de un habito o de una tarea (ver
    ``HABIT_OPTIONS_LAYOUT``/``REAL_HABIT_OPTIONS_LAYOUT``/``TASK_OPTIONS_LAYOUT``):
    lo que se abre al mantener pulsado un habito o una tarea en cualquier
    vista que los muestre (ver ``config.LONG_PRESS_SECONDS`` y
    ``orchestrator.make_key_callback``, que es quien mide la duracion de la
    pulsacion -- este modulo solo describe que hay en cada tecla).

    Un habito y una tarea llevan a pantallas **distintas**
    (``ScreenState.entry_item_kind`` decide cual, ver ``resolve_page``),
    porque sus opciones futuras seran distintas -- una tarea no tiene
    objetivo ni fecha de vencimiento. Un habito, ademas, se resuelve a uno de
    **dos** layouts segun su tipo (ver ``resolve_page``): un ``RealHabit``
    (cuantificable, con objetivo -- p.ej. "Flex") abre
    ``REAL_HABIT_OPTIONS_LAYOUT``, con botones para ajustar el progreso de
    hoy ademas de "Deshacer"; cualquier otro habito (``BooleanHabit`` o
    ``LogHabit``, que no tienen un paso que ajustar en cantidades sueltas)
    abre ``HABIT_OPTIONS_LAYOUT``, solo con "Deshacer". El de una tarea tiene
    tres opciones: cambiar la prioridad, omitirla (skip) e iniciar/detener su
    cronometro. Anadir una opcion mas es anadir una entrada al layout que
    corresponda con su propio ``kind`` y darle significado en
    ``resolve_press``, igual que cualquier otro layout fijo de este modulo.

    Attributes:
        kind: "back" (vuelve a la vista de origen sin tocar el habito/tarea;
            fija en la tecla 0, mismo lugar y mismo rol que la tecla de menu
            en el resto de pantallas), "message" (contenido informativo, no
            interactivo -- ``resolve_press`` la trata como "noop"), "undo"
            (retrocede un paso del habito via ``HabitProvider.undo()`` -- ver
            ``resolve_press``), "add_value" (solo en
            ``REAL_HABIT_OPTIONS_LAYOUT``: suma ``amount`` -- positivo o
            negativo -- al progreso de hoy via ``HabitProvider.set_value()``,
            sin bajar de 0 -- ver ``resolve_press``), "add_step" (solo en
            ``REAL_HABIT_OPTIONS_LAYOUT``: igual que "add_value" pero el
            delta es ``amount`` (``1.0``/``-1.0``, el signo) multiplicado por
            el ``step`` propio del habito, no un valor fijo -- ver
            ``orchestrator.press_habit_options_add_step``), "priority" (solo
            en ``TASK_OPTIONS_LAYOUT``: fija la prioridad de la tarea a
            ``priority`` -- ver ``resolve_press``), "skip" (solo en
            ``TASK_OPTIONS_LAYOUT``: omite la tarea via ``skip_task`` -- ver
            ``resolve_press``), "timer" (solo en ``TASK_OPTIONS_LAYOUT``:
            inicia/detiene el cronometro de la tarea via
            ``TimerProvider.toggle_task_timer()`` -- si esta corriendo,
            ``running``/``started_at`` van rellenos y ``label`` pasa a ser
            el titulo denormalizado de ``running_timer`` (no "Detener
            cronometro"): ``deck.renderer.render_option_entry`` pinta
            ``"titulo\n[tiempo]"`` para no tener que recordar cual esta
            activo; si no, se pinta ``label`` ("Iniciar cronometro"). Lo
            decide ``resolve_page`` sobre una copia del layout, segun si esa
            tarea es la que esta corriendo ahora mismo, ver ahi mismo),
            "toggle_pin" (en la pantalla de opciones de una seccion o de un
            proyecto, ver ``ScreenKind.SECTION_OPTIONS``/
            ``ScreenKind.PROJECT_OPTIONS``: alterna si esa seccion/proyecto
            aparece como boton fijo en el menu principal -- ``label`` ya trae
            "Fijar en menu"/"Quitar del menu" segun el estado actual,
            decidido por ``resolve_page`` contra ``pinned_sections``/
            ``pinned_projects``, ver ahi mismo) o "blank" (tecla vacia).
        label: Texto de la tecla.
        emoji: Icono a color, o cadena vacia.
        priority: Prioridad (``0``/``1``/``3``/``5``) que fija esta tecla.
            Solo tiene sentido si ``kind`` es "priority".
        amount: Solo tiene sentido si ``kind`` es "add_value" (el delta
            exacto a sumar, p.ej. ``1.0``/``-1.0``/``3.0``/``-3.0``/``5.0``/
            ``-5.0``) o "add_step" (el signo por el que multiplicar el
            ``step`` del habito, ``1.0`` o ``-1.0`` -- nunca otro valor).
            ``deck.renderer.render_option_entry`` tambien lo usa para elegir
            color (verde si es positivo, granate si es negativo), sin
            distinguir "add_value" de "add_step".
        running: Solo tiene sentido si ``kind`` es "timer": si ``True``, el
            cronometro de esta tarea esta corriendo ahora mismo.
        started_at: Solo tiene sentido si ``kind`` es "timer" y ``running``
            es ``True``: cuando arranco (ISO 8601 con offset, tal cual lo da
            el proveedor). ``deck.renderer.render_option_entry`` calcula el
            tiempo transcurrido a partir de aqui en cada repintado -- nunca
            un contador que incrementa en el cliente, mismo criterio que
            ``provider.base.TimerLabel.started_at``.
        active: Solo tiene sentido si ``kind`` es "toggle_pin": si ``True``,
            la seccion/proyecto ya esta fijada en el menu principal ahora
            mismo (la tecla dice "Quitar del menu" y se pinta en ambar); si
            ``False``, no lo esta ("Fijar en menu", verde) -- ver
            ``deck.renderer.render_option_entry``. Campo generico (no
            "running", que es especifico de "timer") por si una opcion
            futura de tipo interruptor lo necesita.
    """

    kind: str
    label: str
    emoji: str = ""
    priority: int = 0
    amount: float = 0.0
    running: bool = False
    started_at: str = ""
    active: bool = False


_ITEM_OPTIONS_BACK = OptionEntry("back", "Volver", "↩️")
_ITEM_OPTIONS_BLANK = OptionEntry("blank", "", "")  # relleno de las teclas sin contenido

# Contenido de las pantallas de opciones de un habito y de una tarea (ver
# "Menu y pantallas" en CLAUDE.md): una para cada ``entry_item_kind``, para
# que puedan crecer con opciones distintas sin pisarse. "Volver" fija en la
# tecla 0 en ambas (mismo sitio que la tecla de menu, pero sin abrir el menu
# principal: vuelve a la vista/pagina de origen sin ejecutar ninguna accion
# sobre el habito/tarea que la abrio). Igual que STANDBY_LAYOUT, estos dicts
# son el sitio a tocar para anadir opciones reales; las teclas que no
# aparecen aqui se pintan vacias.
#
# HABIT_OPTIONS_LAYOUT tiene una opcion real, "Deshacer" (tecla 14, la mas
# alejada de "Volver" en la tecla 0 -- separa a proposito una accion
# irreversible-al-tacto de la que saca sin tocar nada): retrocede un paso del
# habito via HabitProvider.undo() -- Boolean -> 0, cuantificable -> value-step
# sin bajar de 0, ver provider.base.HabitProvider.undo. Es generico para
# CUALQUIER Habit (con objetivo o de solo registro), no solo para el caso que
# lo motivo (corregir una pulsacion de mas en un log acumulable como
# "Cocacola"): antes de esto no habia ninguna forma de deshacer un habito
# cuantificable con objetivo en ningun sitio del deck, ni siquiera en
# "Habitos" (su undo por tap solo cubre BooleanHabit, ver
# ViewSpec.allows_undo/_undoes). Ver orchestrator.press_habit_undo_option
# para el porque usa refresh() en vez de exit_item_options().
#
# Es el layout que abre un BooleanHabit o un LogHabit (ver resolve_page): no
# tienen "step" en cantidades sueltas que ajustar, asi que la unica opcion
# real es deshacer. Un RealHabit (cuantificable, con objetivo -- p.ej.
# "Flex") abre en su lugar REAL_HABIT_OPTIONS_LAYOUT, mas abajo.
HABIT_OPTIONS_LAYOUT: dict[int, OptionEntry] = {
    KEY_MENU: _ITEM_OPTIONS_BACK,
    14: OptionEntry("undo", "Deshacer", "⌫"),
}

# Layout de opciones de un habito REAL (cuantificable, con objetivo -- p.ej.
# "Flex"): ademas de "Deshacer" (misma tecla 14 que en HABIT_OPTIONS_LAYOUT,
# para que un habito no cambie de sitio la unica opcion que ambos layouts
# comparten), permite ajustar el progreso de hoy en cantidades sueltas sin
# tener que abrir el teclado numerico (que es solo para habitos
# ``manual_entry``, que ni siquiera llegan a esta pantalla -- ver
# ``resolve_press``). Dos familias de boton, ambas resueltas via
# HabitProvider.set_value() en vez de una RPC nueva (ver
# orchestrator.press_habit_options_add_value/_add_step):
#
# - "add_value" (teclas 1-3 y 6-8): suma/resta un delta fijo -- +1/+3/+5 en
#   la fila de arriba, -1/-3/-5 justo debajo en la misma columna, para que el
#   signo se lea de un vistazo por posicion ademas de por texto y color.
# - "add_step" (teclas 4 y 9, misma columna que las anteriores): suma/resta
#   el "step" propio del habito (el mismo que ya suma un toque corto), para
#   corregir en la unidad natural del habito sin memorizar su valor.
#
# Las teclas 11, 12 y 13 quedan vacias: caben de sobra los 8 botones mas
# "Deshacer" en las 14 teclas disponibles (todas salvo la 0 de "Volver"), y
# dejar hueco alrededor evita que la pantalla se sienta abarrotada. Las
# teclas 5 y 10 SI llevan contenido, pero no estan aqui: son puramente
# informativas (el progreso de hoy sin unidad -- RealHabit.progress_label --
# y la unidad sola, "message" -- resolve_press ya la trata como noop, no
# interactiva) y dependen del habito concreto, asi que resolve_page las
# anade sobre una copia de este dict en vez de vivir en el literal estatico
# -- ver ahi mismo.
REAL_HABIT_OPTIONS_LAYOUT: dict[int, OptionEntry] = {
    KEY_MENU: _ITEM_OPTIONS_BACK,
    1: OptionEntry("add_value", "+1", amount=1.0),
    2: OptionEntry("add_value", "+3", amount=3.0),
    3: OptionEntry("add_value", "+5", amount=5.0),
    4: OptionEntry("add_step", "+Paso", amount=1.0),
    6: OptionEntry("add_value", "-1", amount=-1.0),
    7: OptionEntry("add_value", "-3", amount=-3.0),
    8: OptionEntry("add_value", "-5", amount=-5.0),
    9: OptionEntry("add_step", "-Paso", amount=-1.0),
    14: OptionEntry("undo", "Deshacer", "⌫"),
}

# La tecla 2 ("timer") es un placeholder: si ESTA tarea es la que esta
# corriendo ahora mismo -- algo que solo se sabe en resolve_page, que tiene
# running_timer a mano -- se pinta el tiempo transcurrido en vez del label
# "Iniciar cronometro"; se sobreescribe ahi sobre una copia de este dict,
# mismo patron que las teclas 5/10 informativas de REAL_HABIT_OPTIONS_LAYOUT.
TASK_OPTIONS_LAYOUT: dict[int, OptionEntry] = {
    KEY_MENU: _ITEM_OPTIONS_BACK,
    1: OptionEntry("skip", "Skip", "⏭️"),
    2: OptionEntry("timer", "Iniciar cronometro", "▶️"),
    5: OptionEntry("message", "Prioridad", "🎚️"),
    11: OptionEntry("priority", "Ninguna", priority=0),
    12: OptionEntry("priority", "Baja", priority=1),
    13: OptionEntry("priority", "Media", priority=3),
    14: OptionEntry("priority", "Alta", priority=5),
}


# PageBuilder: (habitos, tareas, plantillas, habitos_log, etiquetas_timer,
#   cronometro_corriendo, totales_de_hoy, totales_de_tarea, mapeo_habito->tecla,
#   pagina)
#   -> (habito_por_tecla, tarea_por_tecla, plantilla_por_tecla,
#       etiqueta_timer_por_tecla, total_de_paginas)
#
# Todos los builders reciben los ocho conjuntos de datos aunque casi ninguno
# los use enteros: es lo que permite que una vista nueva los cruce (p.ej.
# "Crear" necesita las tareas para saber que plantillas ya tienen ocurrencia
# abierta, y "Cronometros" necesita cronometro_corriendo/totales_de_hoy para
# saber que etiqueta resaltar y cuanto tiempo lleva hoy). ``habitos_log``
# (``LogHabit``) va aparte de ``habitos`` a proposito, igual que en
# ``HabitProvider``: ninguna vista existente los esperaba mezclados con los
# habitos de objetivo (ver ``core.screens._log_items``). Un LogHabit que
# acabe en una tecla cae igualmente en ``habito_por_tecla``: ``_place_items``
# reparte por ``isinstance(item.obj, Habit)``, y ``LogHabit`` es un ``Habit``
# mas. ``totales_de_hoy`` es ``TimerProvider.get_daily_totals()`` tal cual:
# segundos acumulados hoy por id de tarea/etiqueta (ver
# ``provider.base.TimerLabel.today_seconds``). ``totales_de_tarea`` es
# ``TimerProvider.get_task_totals()`` tal cual: segundos acumulados de
# SIEMPRE por id de tarea, sin filtrar por dia (ver
# ``provider.base.Task.total_seconds``) -- solo lo usan "Hoy"/"Tareas".
PageBuilder = Callable[
    [
        list[Habit],
        list[Task],
        list[Template],
        list[Habit],
        list[TimerLabel],
        RunningTimer | None,
        dict[str, int],
        dict[str, int],
        dict[str, int],
        int,
    ],
    tuple[dict[int, Habit], dict[int, Task], dict[int, Template], dict[int, TimerLabel], int],
]

# La firma de la funcion de items que envuelve ``_flat_page_builder``.
ItemsFn = Callable[
    [
        list[Habit],
        list[Task],
        list[Template],
        list[Habit],
        list[TimerLabel],
        RunningTimer | None,
        dict[str, int],
        dict[str, int],
    ],
    list[ViewItem],
]


@dataclass(frozen=True)
class ViewSpec:
    """Una vista de datos registrada, tal y como aparece en el menu.

    Attributes:
        id: Id de la vista, la clave con la que se registra en ``VIEWS``.
        menu_label: Texto de su boton en el menu principal.
        menu_emoji: Icono de ese boton.
        build_page: Como reparte sus items entre las teclas de una pagina.
        allows_undo: Si pulsar en esta vista un habito **booleano** ya hecho
            hoy lo deshace en vez de repetir el paso. Lo declara cada vista, no
            se hereda: una vista nueva no deshace nada salvo que lo pida. Solo
            lo usa "Habitos", que es la que muestra los habitos hechos (en
            gris) y sirve por tanto para repasarlos y corregir una pulsacion
            erronea; "Hoy" los oculta, asi que ahi no hay nada que deshacer.
    """

    id: str
    menu_label: str
    menu_emoji: str
    build_page: PageBuilder
    allows_undo: bool = False


def _place_items(
    items: list[ViewItem], reserved_keys: frozenset[int] = frozenset()
) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template], dict[int, TimerLabel]]:
    """Reparte ``items`` (ya recortados a una pagina) entre las teclas
    disponibles, en el orden en que llegan. ``reserved_keys`` se excluye del
    reparto (usado por el sobrante de "Habitos", para no invadir la tecla del
    atajo fijo a "Secciones" -- ver ``KEY_HABITS_SECTIONS_SHORTCUT``)."""
    key_habit: dict[int, Habit] = {}
    key_task: dict[int, Task] = {}
    key_template: dict[int, Template] = {}
    key_timer: dict[int, TimerLabel] = {}
    available_keys = [key for key in AVAILABLE_KEYS if key not in reserved_keys]
    for key, item in zip(available_keys, items, strict=False):
        if isinstance(item.obj, Habit):
            key_habit[key] = item.obj
        elif isinstance(item.obj, Template):
            key_template[key] = item.obj
        elif isinstance(item.obj, TimerLabel):
            key_timer[key] = item.obj
        else:
            key_task[key] = item.obj
    return key_habit, key_task, key_template, key_timer


def _overflow_items(habits: list[Habit], habit_mapping: dict[str, int]) -> list[ViewItem]:
    """Habitos que no cupieron en el mapeo estable de la pagina 0 (lo que
    antes se descartaba sin mas como ``KFUL``), en el mismo orden que usa
    ``core.key_map.update_mapping`` para asignar tecla nueva."""
    overflow_habits = sorted((h for h in habits if h.id not in habit_mapping), key=lambda h: (h.order, h.id))
    return [ViewItem("habit", h) for h in overflow_habits]


def _tiered_page_builder(reserved_keys: frozenset[int] = frozenset()) -> PageBuilder:
    """Constructor de pagina para "Habitos", la unica vista que reutiliza el
    mapeo estable de habitos: la pagina 0 es literalmente el mapeo
    persistido ya calculado por ``core.key_map`` -- por eso un habito
    conserva su tecla exactamente igual entre ciclos, hecho o no. Las paginas
    siguientes son el sobrante (``_overflow_items``), sin garantia de
    estabilidad entre ciclos: es la red de seguridad, no el camino principal.

    ``reserved_keys`` (ver ``KEY_HABITS_SECTIONS_SHORTCUT``) nunca recibe un
    habito, ni en la pagina 0 (el mapeo persistido ya las excluye, ver
    ``core.key_map.update_mapping``) ni en el sobrante (se excluyen tambien
    aqui de ``_place_items``, para que el atajo no se vea invadido por un
    habito en una pagina 1+)."""

    def build(
        habits: list[Habit],
        tasks: list[Task],
        templates: list[Template],
        log_habits: list[Habit],
        timer_labels: list[TimerLabel],
        running_timer: RunningTimer | None,
        daily_totals: dict[str, int],
        task_totals: dict[str, int],
        habit_mapping: dict[str, int],
        page: int,
    ) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template], dict[int, TimerLabel], int]:
        overflow = _overflow_items(habits, habit_mapping)
        overflow_pages = paginate(overflow, 0, PAGE_SIZE)[1] if overflow else 0
        total_pages = 1 + overflow_pages
        page = max(0, min(page, total_pages - 1))

        if page == 0:
            habits_by_id = {h.id: h for h in habits}
            key_habit = {key: habits_by_id[hid] for hid, key in habit_mapping.items() if hid in habits_by_id}
            return key_habit, {}, {}, {}, total_pages

        page_items, _ = paginate(overflow, page - 1, PAGE_SIZE)
        key_habit, key_task, key_template, key_timer = _place_items(page_items, reserved_keys)
        return key_habit, key_task, key_template, key_timer, total_pages

    return build


def _flat_page_builder(items_fn: ItemsFn, reserved_keys: frozenset[int] = frozenset()) -> PageBuilder:
    """Constructor de pagina generico para una vista sin reparto especial:
    pagina la lista completa que devuelva ``items_fn`` sin reutilizar el
    mapeo estable de habitos. Es el que usa una vista nueva por defecto salvo
    que necesite ese mapeo (ver ``_tiered_page_builder``).

    ``reserved_keys`` (usado por "Tareas", ver ``KEY_TASKS_PROJECTS_SHORTCUT``)
    se excluye del reparto en ``_place_items``, mismo papel que en
    ``_tiered_page_builder`` -- ninguna tarea ocupa nunca esa tecla."""

    def build(
        habits: list[Habit],
        tasks: list[Task],
        templates: list[Template],
        log_habits: list[Habit],
        timer_labels: list[TimerLabel],
        running_timer: RunningTimer | None,
        daily_totals: dict[str, int],
        task_totals: dict[str, int],
        habit_mapping: dict[str, int],
        page: int,
    ) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template], dict[int, TimerLabel], int]:
        page_items, total_pages = paginate(
            items_fn(habits, tasks, templates, log_habits, timer_labels, running_timer, daily_totals, task_totals),
            page,
            PAGE_SIZE,
        )
        key_habit, key_task, key_template, key_timer = _place_items(page_items, reserved_keys)
        return key_habit, key_task, key_template, key_timer, total_pages

    return build


def _mark_running_task(
    tasks: list[Task], running_timer: RunningTimer | None, task_totals: dict[str, int]
) -> None:
    """Marca ``Task.timer_running``/``total_seconds`` cruzando ``tasks`` con
    ``running_timer``/``task_totals``, igual que ``_create_items`` marca
    ``Template.has_pending`` o ``_timer_items`` marca ``TimerLabel.running``:
    se muta el objeto antes de envolverlo en un ``ViewItem``, asi
    ``deck.renderer.render_task`` no necesita saber nada de ``RunningTimer``
    ni de ``TimerProvider.get_task_totals()``, solo mira la tarea que ya tiene.

    La usan ``_today_items``/``_tasks_items`` -- las dos vistas que pintan
    tareas como tecla -- no ``_create_items`` (plantillas, no tareas ya
    creadas) ni ``_timer_items`` (etiquetas, no tareas)."""
    running_task_id = running_timer.task_id if running_timer else ""
    for task in tasks:
        task.timer_running = bool(running_task_id) and task.id == running_task_id
        task.total_seconds = task_totals.get(task.id, 0)


def _today_items(
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
) -> list[ViewItem]:
    """Items de la vista "Hoy": habitos pendientes (sin los ya completados
    hoy -- ``is_done``, booleano marcado o cuantificable que alcanzo su
    objetivo -- ordenados por ``(order, id)``, igual que un reparto de
    tecla nuevo) seguidos de las tareas pendientes en el orden que ya trae
    el proveedor (prioridad descendente, fecha ascendente).

    A diferencia de ``habits``, esta vista **no** reutiliza el mapeo estable
    de habitos: se pagina de cero cada vez con ``_flat_page_builder``, asi
    que al completar algo lo que queda se recoloca desde la primera tecla
    disponible, sin dejar hueco -- "Hoy" es la vista que se va vaciando
    durante el dia, no la que conserva la tecla de cada habito."""
    pending_habits = sorted((h for h in habits if not h.is_done), key=lambda h: (h.order, h.id))
    _mark_running_task(tasks, running_timer, task_totals)
    return [ViewItem("habit", h) for h in pending_habits] + [ViewItem("task", t) for t in tasks]


def _tasks_items(
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
) -> list[ViewItem]:
    """Items de la vista "Tareas": solo las tareas pendientes, en el orden que
    ya trae el proveedor."""
    _mark_running_task(tasks, running_timer, task_totals)
    return [ViewItem("task", t) for t in tasks]


def _create_items(
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
) -> list[ViewItem]:
    """Items de la vista "Crear": las plantillas de creacion rapida, todas, en
    el orden que trae el proveedor.

    Aqui vive la unica logica anti-duplicado del daemon. ``instantiate_task`` no
    es idempotente (dos pulsaciones, dos tareas), asi que antes de ofrecer el
    boton se marca cada plantilla que ya tiene una ocurrencia pendiente:
    ``resolve_press`` la convierte en ``noop`` y el renderer la pinta en gris.

    El cruce se hace con las tareas que ya estan en memoria (``template_id`` de
    ``v_today_tasks``), sin ninguna peticion extra. Es tan reciente como el
    ultimo ciclo: una ocurrencia creada desde otro cliente hace un minuto no se
    ve todavia, y eso es aceptable -- el aviso es una red, no un candado.

    A diferencia de "Hoy", las plantillas usadas **no desaparecen**: siguen ahi
    en gris para la proxima vez que toquen.
    """
    pending_template_ids = {t.template_id for t in tasks if t.template_id}
    for template in templates:
        template.has_pending = template.id in pending_template_ids
    return [ViewItem("template", tpl) for tpl in templates]


def _log_items(
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
) -> list[ViewItem]:
    """Items de la vista "Logs": los habitos de solo registro (``LogHabit``),
    todos, ordenados por ``(order, id)`` -- mismo criterio que un reparto de
    tecla nuevo en "Habitos", pero sin necesitar su mapeo persistido: un log
    nunca desaparece de esta lista (no hay ``is_done`` que lo filtre, al
    reves que en "Hoy"), asi que paginar de cero cada ciclo con el mismo
    orden ya da la misma tecla de forma estable mientras no cambien los
    habitos de registro que hay. Por eso esta vista usa ``_flat_page_builder``
    en vez de ``_tiered_page_builder``: no hace falta la persistencia de
    ``core.key_map`` para conseguir el mismo efecto.
    """
    return [ViewItem("habit", h) for h in sorted(log_habits, key=lambda h: (h.order, h.id))]


def _section_menu_entries(habits: list[Habit]) -> list[MenuEntry]:
    """Items del submenu "Secciones" (``ScreenKind.SECTIONS_MENU``): un boton
    por cada nombre de seccion presente en ``habits`` hoy, sacado directamente
    de ``Habit.section_name`` -- **nada de codigo, nada de configuracion**: una
    seccion aparece aqui en cuanto algun habito la tenga asignada
    (``habits.section_id`` en ``../habits-core``) y tenga algo que tocar hoy
    (``v_today_habits`` ya filtra por ``is_due``). Ordenadas alfabeticamente
    (no hay ``sort_order`` de seccion expuesto al cliente).

    Sin emoji propio a proposito: una seccion no tiene icono en ``../habits-core``
    (a diferencia de un habito), y marcar aqui las ya fijadas con un icono
    generico (p.ej. "📌") se descarto -- con varias fijadas todas llevarian el
    mismo icono, sin distinguirlas entre si ni aportar nada que el propio
    menu principal (donde SI aparecen, ver ``_pinned_section_menu_entries``)
    no diga ya.

    Unico camino a una seccion (mantener pulsada una entrada aqui abre su
    pantalla de opciones, ver ``ScreenKind.SECTION_OPTIONS``), junto con el
    atajo fijo de la tecla 1 de "Habitos" que abre este mismo submenu (ver
    ``KEY_HABITS_SECTIONS_SHORTCUT``): no hay entrada de seccion en el menu
    principal salvo la que el propio usuario fije (ver
    ``_pinned_section_menu_entries``)."""
    names = sorted({h.section_name.strip() for h in habits if h.section_name.strip()})
    return [MenuEntry(name, "", "enter_section", section_name=name) for name in names]


def _pinned_section_menu_entries(habits: list[Habit], pinned_sections: frozenset[str]) -> list[MenuEntry]:
    """Botones fijos en el menu principal para las secciones marcadas como
    favoritas desde su pantalla de opciones (``ScreenKind.SECTION_OPTIONS``,
    tecla "Fijar en menu"/"Quitar del menu") -- alternativa dinamica y local
    al deck a lo que antes era ``PINNED_SECTIONS`` (una constante en codigo
    que exigia desplegar para cambiar).

    Sin emoji a proposito (mismo motivo que ``_section_menu_entries``): una
    seccion no tiene icono propio en ``../habits-core``, y forzar uno
    generico (p.ej. "📌") en todas las fijadas las volveria indistinguibles
    entre si con varias a la vez -- se mantiene el texto solo, como el resto
    de entradas de "Secciones".

    Solo se devuelve un boton por cada nombre de ``pinned_sections`` que siga
    teniendo algun habito hoy (mismo criterio que ``_section_menu_entries``):
    una seccion despinneada sola porque ya no tiene habitos no deja un boton
    muerto en el menu. El nombre mostrado sale de ``habits`` (el mas fresco),
    no del guardado en ``pinned_sections`` (normalizado, solo para comparar).

    Se usa en la rama ``ScreenKind.MENU`` de ``resolve_page``, concatenada a
    ``MENU_ENTRIES`` antes de repartir teclas con ``_nav_page`` -- que ya
    pagina sola si no caben todas en la pagina 0, sin necesitar ningun cambio
    ahi."""
    by_key: dict[str, str] = {}
    for h in habits:
        name = h.section_name.strip()
        if name:
            by_key.setdefault(name.lower(), name)
    names = sorted(by_key[key] for key in pinned_sections if key in by_key)
    return [MenuEntry(name, "", "enter_section", section_name=name) for name in names]


def _section_page(section_name: str, habits: list[Habit], page: int) -> ResolvedPage:
    """Resuelve una seccion filtrada (``ScreenState.section_name``): mismo
    comportamiento que "Habitos" (se ven todos, hechos hoy en gris, y
    ``_undoes`` siempre deja deshacer un booleano ya hecho aqui), pero
    restringido a los habitos cuya ``section_name`` coincide (comparacion
    insensible a mayusculas/minusculas y a espacios sueltos, para no depender
    de que la entrada pulsada en "Secciones" (``_section_menu_entries``)
    escribiera el nombre exactamente igual).

    A diferencia de una vista de ``VIEWS``, esto **no** pasa por
    ``ViewSpec``/``PageBuilder``: el filtro es un parametro en tiempo de
    ejecucion (el nombre pulsado), no un id fijo registrado de antemano. Mismo
    truco interno que ``_flat_page_builder`` (``paginate`` + ``_place_items``),
    sin necesitar el mapeo persistido de ``core.key_map`` -- ningun habito
    desaparece de aqui solo por completarse hoy, asi que la tecla de cada uno
    ya sale estable entre ciclos sin esa persistencia, igual que "Logs"."""
    target = section_name.strip().lower()
    matching = [h for h in habits if h.section_name.strip().lower() == target]
    page_items, total_pages = paginate(
        [ViewItem("habit", h) for h in sorted(matching, key=lambda h: (h.order, h.id))], page, PAGE_SIZE
    )
    key_habit, key_task, key_template, key_timer = _place_items(page_items)
    return ResolvedPage(
        key_habit=key_habit,
        key_task=key_task,
        key_template=key_template,
        key_timer=key_timer,
        page=_clamp_page(page, total_pages),
        total_pages=total_pages,
    )


def _project_menu_entries(tasks: list[Task]) -> list[MenuEntry]:
    """Items del submenu "Proyectos" (``ScreenKind.PROJECTS_MENU``): un boton
    por cada nombre de proyecto presente en ``tasks`` hoy, sacado directamente
    de ``Task.project_name`` -- **nada de codigo, nada de configuracion**: un
    proyecto aparece aqui en cuanto alguna tarea lo tenga asignada
    (``tasks.project_id`` en ``../habits-core``) y venza hoy o antes
    (``v_today_tasks`` ya filtra). Ordenados alfabeticamente (no hay
    ``sort_order`` de proyecto expuesto al cliente). Mirror exacto de
    ``_section_menu_entries`` para tareas -- mismo razonamiento sobre no
    llevar emoji propio (ver ahi mismo).

    Unico camino a un proyecto (mantener pulsada una entrada aqui abre su
    pantalla de opciones, ver ``ScreenKind.PROJECT_OPTIONS``), junto con el
    atajo fijo de la tecla 1 de "Tareas" que abre este mismo submenu (ver
    ``KEY_TASKS_PROJECTS_SHORTCUT``): no hay entrada de proyecto en el menu
    principal salvo la que el propio usuario fije (ver
    ``_pinned_project_menu_entries``)."""
    names = sorted({t.project_name.strip() for t in tasks if t.project_name.strip()})
    return [MenuEntry(name, "", "enter_project", project_name=name) for name in names]


def _pinned_project_menu_entries(tasks: list[Task], pinned_projects: frozenset[str]) -> list[MenuEntry]:
    """Botones fijos en el menu principal para los proyectos marcados como
    favoritos desde su pantalla de opciones (``ScreenKind.PROJECT_OPTIONS``,
    tecla "Fijar en menu"/"Quitar del menu"). Mirror exacto de
    ``_pinned_section_menu_entries`` para tareas.

    Solo se devuelve un boton por cada nombre de ``pinned_projects`` que siga
    teniendo alguna tarea hoy (mismo criterio que ``_project_menu_entries``):
    un proyecto despinneado solo porque ya no tiene tareas no deja un boton
    muerto en el menu.

    Se usa en la rama ``ScreenKind.MENU`` de ``resolve_page``, concatenada a
    ``MENU_ENTRIES``/``_pinned_section_menu_entries`` antes de repartir
    teclas con ``_nav_page``."""
    by_key: dict[str, str] = {}
    for t in tasks:
        name = t.project_name.strip()
        if name:
            by_key.setdefault(name.lower(), name)
    names = sorted(by_key[key] for key in pinned_projects if key in by_key)
    return [MenuEntry(name, "", "enter_project", project_name=name) for name in names]


def _project_page(
    project_name: str,
    tasks: list[Task],
    running_timer: RunningTimer | None,
    task_totals: dict[str, int],
    page: int,
) -> ResolvedPage:
    """Resuelve un proyecto filtrado (``ScreenState.project_name``): mismo
    comportamiento que "Tareas" (sin deshacer, una tarea nunca lo tiene),
    pero restringido a las tareas cuyo ``project_name`` coincide (comparacion
    insensible a mayusculas/minusculas y a espacios sueltos, igual que
    ``_section_page``). Se conserva el orden que ya trae el proveedor
    (prioridad descendente, fecha ascendente) en vez de reordenar, mismo
    criterio que ``_tasks_items``.

    A diferencia de ``_section_page``, tambien marca ``timer_running``/
    ``total_seconds`` (``_mark_running_task``) antes de repartir: una tarea
    filtrada por proyecto pinta el mismo borde/prefijo de cronometro que en
    "Tareas", que ``_tasks_items`` ya hace por su cuenta pero que aqui, al no
    pasar por ningun ``PageBuilder``, hay que llamar explicitamente."""
    target = project_name.strip().lower()
    matching = [t for t in tasks if t.project_name.strip().lower() == target]
    _mark_running_task(matching, running_timer, task_totals)
    page_items, total_pages = paginate([ViewItem("task", t) for t in matching], page, PAGE_SIZE)
    key_habit, key_task, key_template, key_timer = _place_items(page_items)
    return ResolvedPage(
        key_habit=key_habit,
        key_task=key_task,
        key_template=key_template,
        key_timer=key_timer,
        page=_clamp_page(page, total_pages),
        total_pages=total_pages,
    )


def _timer_items(
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
) -> list[ViewItem]:
    """Items de la vista "Cronometros": las etiquetas rapidas, todas, en el
    orden que trae el proveedor (``sort_order``/``id``, mismo criterio que un
    reparto de tecla nuevo).

    Antes de repartirlas, se marca cual esta corriendo ahora mismo cruzando
    contra ``running_timer`` (mutando ``running``/``started_at`` sobre cada
    ``TimerLabel``, igual que ``_create_items`` mutando ``has_pending`` en
    cada ``Template``) -- asi ``deck.renderer.render_timer`` no necesita saber
    nada de ``RunningTimer``, solo mira el objeto que ya tiene. Se cruza
    tambien contra ``daily_totals`` (``today_seconds``, ver
    ``provider.base.TimerLabel``): ``deck.renderer.render_timer`` solo lo
    pinta si la etiqueta NO esta corriendo -- mientras corre ya se ve el
    tiempo transcurrido de esta sesion.

    Igual que "Logs": una etiqueta no desaparece de esta lista salvo que se
    archive (fuera del alcance de este cliente), asi que paginar de cero cada
    ciclo ya da tecla estable sin necesitar ``core.key_map``.
    """
    running_label_id = running_timer.label_id if running_timer else ""
    for label in timer_labels:
        label.running = bool(running_label_id) and label.id == running_label_id
        label.started_at = running_timer.started_at if label.running else ""
        label.today_seconds = daily_totals.get(label.id, 0)
    return [ViewItem("timer_label", tl) for tl in sorted(timer_labels, key=lambda t: (t.order, t.id))]


KEY_HABITS_SECTIONS_SHORTCUT = 1
"""Tecla fija de la vista "Habitos" para el atajo directo a "Secciones"
(``ScreenKind.SECTIONS_MENU``) -- unico punto de entrada a una seccion, no
hay nada equivalente en el menu principal. Reservada de forma permanente,
como ``KEY_TIMER_SHORTCUT`` en el menu: ningun habito la ocupa nunca, ni en
la pagina 0 (``core.key_map.update_mapping`` la excluye) ni en
el sobrante (``_place_items`` tambien). El contenido en si (el boton) solo se
pinta en la pagina 0, igual que ``KEY_TIMER_SHORTCUT`` -- ver ``resolve_page``."""

KEY_TASKS_PROJECTS_SHORTCUT = 1
"""Tecla fija de la vista "Tareas" para el atajo directo a "Proyectos"
(``ScreenKind.PROJECTS_MENU``) -- mismo papel que
``KEY_HABITS_SECTIONS_SHORTCUT`` en "Habitos", pero "Tareas" usa
``_flat_page_builder`` (no reutiliza ningun mapeo persistido, ver
``core.key_map``), asi que la reserva se hace pasando ``reserved_keys`` a
``_flat_page_builder`` en vez de a ``core.key_map.update_mapping``."""

VIEWS: dict[str, ViewSpec] = {
    "today": ViewSpec("today", "Hoy", "📅", _flat_page_builder(_today_items)),
    "habits": ViewSpec(
        "habits", "Habitos", "✅", _tiered_page_builder(frozenset({KEY_HABITS_SECTIONS_SHORTCUT})), allows_undo=True
    ),
    "tasks": ViewSpec(
        "tasks", "Tareas", "🗒️", _flat_page_builder(_tasks_items, frozenset({KEY_TASKS_PROJECTS_SHORTCUT}))
    ),
    "create": ViewSpec("create", "Crear", "➕", _flat_page_builder(_create_items)),
    "logs": ViewSpec("logs", "Logs", "📝", _flat_page_builder(_log_items)),
    "timers": ViewSpec("timers", "Cronometros", "⏱️", _flat_page_builder(_timer_items)),
}
DEFAULT_VIEW_ID = "today"

MENU_ENTRIES: list[MenuEntry] = [
    MenuEntry(VIEWS["today"].menu_label, VIEWS["today"].menu_emoji, "select_view", view_id="today", key=1),
    MenuEntry(VIEWS["habits"].menu_label, VIEWS["habits"].menu_emoji, "select_view", view_id="habits", key=2),
    MenuEntry(VIEWS["tasks"].menu_label, VIEWS["tasks"].menu_emoji, "select_view", view_id="tasks"),
    MenuEntry(VIEWS["create"].menu_label, VIEWS["create"].menu_emoji, "select_view", view_id="create"),
    MenuEntry(VIEWS["logs"].menu_label, VIEWS["logs"].menu_emoji, "select_view", view_id="logs"),
    # Tecla fija 8, junto al atajo de cronometro fijo en la 7 (ver
    # KEY_TIMER_SHORTCUT/_timer_shortcut_item): las dos van pegadas a
    # proposito, la vista completa al lado de su acceso directo.
    MenuEntry(VIEWS["timers"].menu_label, VIEWS["timers"].menu_emoji, "select_view", view_id="timers", key=8),
    # PoC independiente de habits-core (ver ScreenKind.TICKTICK): sin tecla
    # fija, se reparte como Tareas/Crear/Logs.
    MenuEntry("TickTick", "☑️", "open_ticktick"),
    MenuEntry("Sistema", "⚙️", "open_system", key=14),
]

KEY_TIMER_SHORTCUT = 7
"""Tecla fija del menu principal para el atajo al cronometro (ver
``_timer_shortcut_item``): reservada solo en ``ScreenKind.MENU``, junto a la
tecla 8 fija de "Cronometros" (``MENU_ENTRIES``) -- no es un ``MenuEntry``
(no navega a ninguna vista, alterna un cronometro), asi que se excluye del
reparto automatico via ``reserved_keys`` en vez de ocupar una entrada."""

_TIMER_SHORTCUT_EMPTY_ID = "__timer_shortcut_empty__"
"""Id que nunca coincide con una tarea/etiqueta real (ver
``provider.base.TimerLabel``/``TimerProvider``): lo que pinta la tecla 7
cuando ``last_timer`` es ``None`` (nunca se ha usado ningun cronometro desde
que arranco el daemon, o el que se recordaba ya no existe -- ver
``orchestrator._prune_stale_last_timer``). Pulsarla no encuentra tarea ni
etiqueta con ese id (``orchestrator.press_timer_toggle``), asi que no hace
nada -- sin necesitar ningun caso especial en el pulsado."""

_TIMER_SHORTCUT_EMPTY_LABEL = "Sin cronometro"


def _timer_shortcut_item(
    last_timer: RunningTimer | None, running_timer: RunningTimer | None, daily_totals: dict[str, int]
) -> TimerLabel:
    """Contenido de la tecla 7 del menu principal (``KEY_TIMER_SHORTCUT``):
    acceso directo al cronometro actual o al ultimo que se uso, para no tener
    que acordarse de que hay uno corriendo en segundo plano.

    Devuelve siempre un ``TimerLabel`` (nunca ``None``): si ``last_timer`` es
    ``None`` -- nunca se ha usado un cronometro desde que arranco el daemon,
    o el que se recordaba ya no existe y ``orchestrator._prune_stale_last_timer``
    lo olvido -- se pinta un aviso fijo ("Sin cronometro", id que no coincide
    con nada real) en vez de dejar la tecla vacia.

    Si no, se reutiliza el titulo/started_at de ``last_timer`` (ya
    denormalizado, sin emoji -- ver ``provider.supabase.build_running_timer``)
    y se decide correr/parado SOLO mirando si ``running_timer`` es ``None``:
    por construccion (ver ``orchestrator.refresh_cycle``), cuando hay un
    cronometro corriendo ``last_timer`` y ``running_timer`` son siempre el
    mismo objeto, asi que no hace falta comparar sus ids. ``today_seconds``
    sale de ``daily_totals`` por el mismo id -- igual que en "Cronometros"
    (``_timer_items``), solo tiene efecto visible mientras esta parado (ver
    ``deck.renderer.render_timer_shortcut``).
    """
    if last_timer is None:
        return TimerLabel(id=_TIMER_SHORTCUT_EMPTY_ID, name=_TIMER_SHORTCUT_EMPTY_LABEL)
    is_running = running_timer is not None
    entity_id = last_timer.task_id or last_timer.label_id
    return TimerLabel(
        id=entity_id,
        name=clip_title(last_timer.title),
        running=is_running,
        started_at=last_timer.started_at if is_running else "",
        today_seconds=daily_totals.get(entity_id, 0),
    )


# "Suspender" va antes que "Apagar" a proposito: la accion reversible se queda
# con la tecla mas accesible y la irreversible no hereda la posicion de la que
# se pulsa a menudo. El color tambien las separa (azul de navegacion vs. rojo de
# aviso, ver ``deck.renderer.render_nav_entry``).
SYSTEM_ENTRIES: list[MenuEntry] = [
    MenuEntry("Suspender", "🌙", "standby"),
    MenuEntry("Apagar", "🔴", "shutdown"),
]


@dataclass(frozen=True)
class ResolvedPage:
    """La pagina activa ya resuelta: que pintar en cada tecla."""

    key_habit: dict[int, Habit] = field(default_factory=dict)
    key_task: dict[int, Task] = field(default_factory=dict)
    key_template: dict[int, Template] = field(default_factory=dict)
    key_timer: dict[int, TimerLabel] = field(default_factory=dict)
    # Solo lo rellena ScreenKind.MENU, en KEY_TIMER_SHORTCUT (ver
    # _timer_shortcut_item): aparte de key_timer (las etiquetas de la vista
    # "Cronometros") porque su pintado es distinto -- gris de "en espera" en
    # vez de turquesa de "parado, listo para arrancar" (ver
    # deck.renderer.render_timer_shortcut) -- aunque el objeto sea el mismo
    # tipo, TimerLabel.
    key_timer_shortcut: dict[int, TimerLabel] = field(default_factory=dict)
    # La pantalla principal de "TickTick" tambien lo usa, para sus botones de
    # proyecto (ver resolve_page): son MenuEntry con action="enter_ticktick_project",
    # pintados con render_nav_entry igual que cualquier otro boton de navegacion.
    key_nav: dict[int, MenuEntry] = field(default_factory=dict)
    key_numeric: dict[int, NumericKey] = field(default_factory=dict)
    key_standby: dict[int, StandbyKey] = field(default_factory=dict)
    key_options: dict[int, OptionEntry] = field(default_factory=dict)
    # Solo lo rellena ScreenKind.TICKTICK (ver resolve_page): tareas de
    # TickTick, independientes de todo lo demas (ver ticktick/base.py). En la
    # pantalla principal, solo las que no caen bajo ningun boton de proyecto
    # de key_nav (Inbox y tareas de un proyecto cerrado); en la filtrada por
    # proyecto (ScreenState.ticktick_project_id), solo las de ese proyecto.
    key_ticktick: dict[int, TickTickTask] = field(default_factory=dict)
    page: int = 0
    total_pages: int = 1


def _clamp_page(page: int, total_pages: int) -> int:
    return max(0, min(page, total_pages - 1))


def _nav_page(
    entries: list[MenuEntry], page: int, reserved_keys: frozenset[int] = frozenset()
) -> tuple[dict[int, MenuEntry], int]:
    """Reparte ``entries`` entre las teclas de la pagina.

    ``reserved_keys`` se excluye del reparto automatico igual que una tecla
    fija (``MenuEntry.key``), pero sin ocupar ninguna entrada: la usa
    ``ScreenKind.MENU`` para reservar ``KEY_TIMER_SHORTCUT``, que no es un
    ``MenuEntry`` (ver ``resolve_page``). ``ScreenKind.SYSTEM`` la deja vacia,
    sin reservar nada.

    Las que traen ``key`` fijo (ver ``MenuEntry.key``) van siempre ahi, y solo
    en la pagina 0; el resto se reparte por las teclas restantes en el orden
    en que aparecen en la lista.
    """
    fixed = {entry.key: entry for entry in entries if entry.key is not None}
    auto_entries = [entry for entry in entries if entry.key is None]
    free_keys = [key for key in AVAILABLE_KEYS if key not in fixed and key not in reserved_keys]
    auto_page, total_pages = paginate(auto_entries, page, len(free_keys))
    key_nav = dict(zip(free_keys, auto_page, strict=False))
    if page == 0:
        key_nav.update(fixed)
    return key_nav, total_pages


def resolve_page(
    screen: ScreenState,
    habits: list[Habit],
    tasks: list[Task],
    templates: list[Template],
    log_habits: list[Habit],
    timer_labels: list[TimerLabel],
    running_timer: RunningTimer | None,
    daily_totals: dict[str, int],
    task_totals: dict[str, int],
    last_timer: RunningTimer | None,
    habit_mapping: dict[str, int],
    pinned_sections: frozenset[str],
    pinned_projects: frozenset[str],
    ticktick_tasks: list[TickTickTask],
    ticktick_projects: list[TickTickProject],
) -> ResolvedPage:
    """Resuelve la pantalla/pagina activa contra los datos vigentes.

    Args:
        screen: Pantalla activa (menu, sistema o una vista con su pagina).
        habits: Habitos del ultimo ``get_habits()`` exitoso.
        tasks: Tareas del ultimo ``get_tasks()`` exitoso.
        templates: Plantillas del ultimo ``get_templates()`` exitoso.
        log_habits: Habitos de solo registro (``LogHabit``) del ultimo
            ``get_log_habits()`` exitoso. Solo los usa la vista "Logs", pero
            todo ``PageBuilder`` los recibe igual que el resto de conjuntos
            de datos (ver ``core.screens.PageBuilder``).
        timer_labels: Etiquetas rapidas de cronometro del ultimo
            ``get_timer_labels()`` exitoso. Solo las usa la vista
            "Cronometros", mismo criterio que ``log_habits``.
        running_timer: El cronometro en marcha ahora mismo (o ``None``) del
            ultimo ``get_running_timer()`` exitoso. Lo usan tanto "Cronometros"
            (que etiqueta resaltar) como el menu de opciones de una tarea (si
            esta corriendo, para decidir "Iniciar" vs "Detener") y el atajo
            de la tecla 7 del menu (para saber si lo que recuerda esta
            corriendo ahora mismo, ver ``_timer_shortcut_item``).
        daily_totals: Segundos acumulados hoy por id de tarea/etiqueta, del
            ultimo ``get_daily_totals()`` exitoso (``id -> segundos``, ausente
            = 0). Lo usan "Cronometros" (``_timer_items``) y el atajo de la
            tecla 7 (``_timer_shortcut_item``) para pintar el total del dia
            en una tecla parada -- ver ``provider.base.TimerLabel.today_seconds``.
        task_totals: Segundos acumulados de SIEMPRE por id de tarea (sin
            filtrar por dia, al reves que ``daily_totals``), del ultimo
            ``get_task_totals()`` exitoso. Solo lo usan "Hoy"/"Tareas"
            (``_mark_running_task``) para pintar el acumulado historico en la
            propia tecla de la tarea -- ver ``provider.base.Task.total_seconds``.
        last_timer: El ultimo cronometro no vacio visto por
            ``orchestrator`` (``last_timer_ref``, ver ahi mismo), o ``None``
            si no se ha usado ninguno todavia (o el que se recordaba dejo de
            existir y se olvido, ver ``orchestrator._prune_stale_last_timer``).
            Solo lo usa ``ScreenKind.MENU`` para la tecla 7
            (``KEY_TIMER_SHORTCUT``).
        habit_mapping: Mapeo persistido habito -> tecla vigente.
        pinned_sections: Nombres de seccion (normalizados, ``strip().lower()``)
            marcados como favoritos desde ``ScreenKind.SECTION_OPTIONS`` (ver
            ``core.pinned_sections``). Los usa ``ScreenKind.MENU``
            (``_pinned_section_menu_entries``, para anadir sus botones fijos)
            y ``ScreenKind.SECTION_OPTIONS`` (para decidir la etiqueta "Fijar
            en menu"/"Quitar del menu"). ``ScreenKind.SECTIONS_MENU`` no lo
            necesita: esa lista no marca de ningun modo las ya fijadas (ver
            ``_section_menu_entries``).
        pinned_projects: Nombres de proyecto (normalizados) marcados como
            favoritos desde ``ScreenKind.PROJECT_OPTIONS`` (ver
            ``core.pinned_projects``). Mismo papel que ``pinned_sections``,
            para ``ScreenKind.MENU``/``ScreenKind.PROJECT_OPTIONS``.
        ticktick_tasks: Tareas de TickTick del ultimo
            ``orchestrator.ticktick_refresh_cycle()`` exitoso. Solo las usa
            ``ScreenKind.TICKTICK``; el resto de pantallas la reciben igual
            (mismo criterio que ``log_habits``/``timer_labels``) pero no la
            miran.
        ticktick_projects: Proyectos de TickTick del mismo
            ``ticktick_refresh_cycle()`` exitoso que ``ticktick_tasks``.
            Tambien solo los usa ``ScreenKind.TICKTICK``, para los botones de
            la pantalla principal (ver mas abajo).

    Returns:
        La pagina resuelta, lista para pintar con ``deck.renderer.render_page``.
    """
    if screen.kind is ScreenKind.STANDBY:
        # La pantalla de suspension es fija: no depende de habitos, tareas ni
        # plantillas. Se resuelve a las 15 teclas (las que STANDBY_LAYOUT no
        # define quedan en blanco) por el mismo motivo que el teclado numerico:
        # asi ``render_page`` la trata como un bloque y las teclas 0/5/10 no
        # recaen en menu/paginacion, que aqui no significan nada.
        key_standby = {key: STANDBY_LAYOUT.get(key, _STANDBY_BLANK) for key in ALL_KEYS}
        return ResolvedPage(key_standby=key_standby, page=0, total_pages=1)
    if screen.kind is ScreenKind.MENU:
        entries = (
            MENU_ENTRIES
            + _pinned_section_menu_entries(habits, pinned_sections)
            + _pinned_project_menu_entries(tasks, pinned_projects)
        )
        key_nav, total_pages = _nav_page(entries, screen.page, reserved_keys=frozenset({KEY_TIMER_SHORTCUT}))
        clamped_page = _clamp_page(screen.page, total_pages)
        # KEY_TIMER_SHORTCUT, como las teclas fijas de MENU_ENTRIES, solo en
        # la pagina 0 -- reservarla en _nav_page ya la deja fuera de key_nav
        # en cualquier pagina, esto es solo lo que la rellena en la primera.
        key_timer_shortcut = (
            {KEY_TIMER_SHORTCUT: _timer_shortcut_item(last_timer, running_timer, daily_totals)}
            if clamped_page == 0
            else {}
        )
        return ResolvedPage(
            key_nav=key_nav, key_timer_shortcut=key_timer_shortcut, page=clamped_page, total_pages=total_pages
        )
    if screen.kind is ScreenKind.SYSTEM:
        key_nav, total_pages = _nav_page(SYSTEM_ENTRIES, screen.page)
        return ResolvedPage(key_nav=key_nav, page=_clamp_page(screen.page, total_pages), total_pages=total_pages)
    if screen.kind is ScreenKind.SECTIONS_MENU:
        # Misma mecanica que ScreenKind.SYSTEM, salvo que la lista no es un
        # literal estatico: sale de _section_menu_entries(habits), recalculada
        # en cada resolucion contra los habitos vigentes.
        key_nav, total_pages = _nav_page(_section_menu_entries(habits), screen.page)
        return ResolvedPage(key_nav=key_nav, page=_clamp_page(screen.page, total_pages), total_pages=total_pages)
    if screen.kind is ScreenKind.PROJECTS_MENU:
        # Mismo mecanismo que ScreenKind.SECTIONS_MENU, para tareas.
        key_nav, total_pages = _nav_page(_project_menu_entries(tasks), screen.page)
        return ResolvedPage(key_nav=key_nav, page=_clamp_page(screen.page, total_pages), total_pages=total_pages)
    if screen.kind is ScreenKind.NUMERIC_ENTRY:
        key_numeric = dict(NUMERIC_KEYPAD)
        key_numeric[1] = NumericKey("display", screen.entry_value)
        return ResolvedPage(key_numeric=key_numeric, page=0, total_pages=1)
    if screen.kind is ScreenKind.ITEM_OPTIONS:
        # Mismo patron que NUMERIC_ENTRY/STANDBY: se resuelve a las 15 teclas
        # (lo que el layout no define queda en blanco) para que la tecla 0 se
        # reinterprete como "Volver" y render_page trate la pantalla entera
        # como un bloque, sin recaer en menu/paginacion. Habito y tarea usan
        # layouts distintos -- sus opciones futuras seran distintas -- y un
        # habito, ademas, elige entre dos segun su tipo: un RealHabit (busca
        # su objeto en habits+log_habits por entry_item_id, ya en memoria de
        # este mismo ciclo/navegacion) abre REAL_HABIT_OPTIONS_LAYOUT, con
        # botones para ajustar el progreso mas las teclas 5/10 informativas
        # (progreso de hoy y unidad, ver mas abajo); cualquier otro
        # (BooleanHabit, LogHabit, o si el habito ya desaparecio entre
        # ciclos) cae al HABIT_OPTIONS_LAYOUT de siempre, solo con "Deshacer".
        if screen.entry_item_kind == "habit":
            habit = next((h for h in (*habits, *log_habits) if h.id == screen.entry_item_id), None)
            if isinstance(habit, RealHabit):
                # Copia porque las teclas 5/10 son informativas y dependen
                # del habito concreto (progreso de hoy y unidad): no tiene
                # sentido que vivan en el literal estatico de mas arriba, que
                # no conoce ningun habito en particular. "message" ya se
                # trata como noop en resolve_press, igual que la cabecera
                # "Prioridad" de TASK_OPTIONS_LAYOUT. Sin emoji en ninguna de
                # las dos: es contenido puramente textual, no un boton mas.
                layout = dict(REAL_HABIT_OPTIONS_LAYOUT)
                layout[5] = OptionEntry("message", habit.progress_label)
                layout[10] = OptionEntry("message", habit.unit)
            else:
                layout = HABIT_OPTIONS_LAYOUT
        else:
            # Tecla 2 ("timer"): si ESTA tarea es la que esta corriendo ahora
            # mismo, se rellenan running/started_at y el label pasa a ser el
            # titulo denormalizado de running_timer (no "Detener cronometro"):
            # deck.renderer.render_option_entry pinta "titulo\n[tiempo]" para
            # no tener que recordar cual esta activo -- copia del dict, mismo
            # patron que las teclas 5/10 informativas del RealHabit de arriba.
            layout = dict(TASK_OPTIONS_LAYOUT)
            is_running = running_timer is not None and running_timer.task_id == screen.entry_item_id
            layout[2] = (
                OptionEntry("timer", clip_title(running_timer.title), running=True, started_at=running_timer.started_at)
                if is_running
                else OptionEntry("timer", "Iniciar cronometro", "▶️")
            )
        key_options = {key: layout.get(key, _ITEM_OPTIONS_BLANK) for key in ALL_KEYS}
        return ResolvedPage(key_options=key_options, page=0, total_pages=1)

    if screen.kind is ScreenKind.SECTION_OPTIONS:
        # Mismo patron que ITEM_OPTIONS (resuelve a las 15 teclas via
        # key_options, reutilizando deck.renderer.render_option_entry sin
        # tocar el renderer), pero pantalla propia: aqui "Volver" tiene que
        # regresar a ScreenKind.SECTIONS_MENU, no a ScreenKind.VIEW, asi que
        # no puede compartir el mecanismo generico de ITEM_OPTIONS (ver
        # ScreenState.entry_section_name).
        is_pinned = screen.entry_section_name.strip().lower() in pinned_sections
        layout = {
            KEY_MENU: _ITEM_OPTIONS_BACK,
            14: OptionEntry(
                "toggle_pin", "Quitar del menu" if is_pinned else "Fijar en menu", "📌", active=is_pinned
            ),
        }
        key_options = {key: layout.get(key, _ITEM_OPTIONS_BLANK) for key in ALL_KEYS}
        return ResolvedPage(key_options=key_options, page=0, total_pages=1)

    if screen.kind is ScreenKind.PROJECT_OPTIONS:
        # Mismo patron que ScreenKind.SECTION_OPTIONS, para un proyecto: aqui
        # "Volver" tiene que regresar a screen.entry_project_origin, no a
        # ScreenKind.VIEW, mismo motivo que la seccion (ver
        # ScreenState.entry_project_name).
        is_pinned = screen.entry_project_name.strip().lower() in pinned_projects
        layout = {
            KEY_MENU: _ITEM_OPTIONS_BACK,
            14: OptionEntry(
                "toggle_pin", "Quitar del menu" if is_pinned else "Fijar en menu", "📌", active=is_pinned
            ),
        }
        key_options = {key: layout.get(key, _ITEM_OPTIONS_BLANK) for key in ALL_KEYS}
        return ResolvedPage(key_options=key_options, page=0, total_pages=1)

    if screen.kind is ScreenKind.TICKTICK:
        # Tecla 5/10 normales (paginacion): con solo dos tipos de contenido
        # (boton de proyecto, tarea) sobra sitio. La tecla 0 SI se reinterpreta,
        # pero solo filtrada a un proyecto (ver justo abajo): en la principal
        # sigue siendo el menu de siempre.
        if screen.ticktick_project_id:
            # Filtrada a un proyecto (ScreenState.ticktick_project_id, ver su
            # docstring): mismo orden que la principal, pero sin botones.
            # Tecla 0 = "Volver" a la principal (reutiliza el mismo
            # OptionEntry/render_option_entry que el resto de "Volver" del
            # deck, ver _ITEM_OPTIONS_BACK) en vez de abrir el menu principal
            # -- resolve_press la resuelve a "exit_ticktick_project" ANTES
            # que a "open_menu" (mismo orden que ITEM_OPTIONS/NUMERIC_ENTRY),
            # asi que no hace falta rellenar el resto de teclas: las que no
            # esten en key_options caen en key_ticktick como siempre.
            # Orden SIN t.completed a proposito: completar una tarea no debe
            # moverla de tecla (ver comentario igual en las tareas sueltas,
            # unas lineas mas abajo).
            matching = [t for t in ticktick_tasks if t.project_id == screen.ticktick_project_id]
            ordered_tasks = sorted(matching, key=lambda t: (-t.priority, t.id))
            page_items, total_pages = paginate(ordered_tasks, screen.page, PAGE_SIZE)
            key_ticktick = dict(zip(AVAILABLE_KEYS, page_items, strict=False))
            return ResolvedPage(
                key_options={KEY_MENU: _ITEM_OPTIONS_BACK},
                key_ticktick=key_ticktick,
                page=_clamp_page(screen.page, total_pages),
                total_pages=total_pages,
            )

        # Principal: un boton (azul de navegacion, mismo mecanismo que
        # "enter_section"/"enter_project") por cada proyecto no cerrado,
        # ordenados como en la propia TickTick (sort_order), seguidos de las
        # tareas sin proyecto reconocido -- el Inbox (que no tiene entrada en
        # GET /project, ver ticktick/client.py) y cualquier tarea de un
        # proyecto closed (ver ticktick/base.py::TickTickProject.closed) --
        # ordenadas por prioridad/id SIN mirar si estan completadas: una
        # completada se queda en su misma tecla, solo en gris, hasta el
        # proximo refresco real (ver orchestrator.press_ticktick_toggle) en
        # vez de saltar al final y recolocar el resto. Proyectos y tareas se paginan
        # juntos, como una sola lista: los proyectos siempre caen en las
        # primeras teclas de la pagina 0 porque van primero en la lista.
        open_projects = sorted((p for p in ticktick_projects if not p.closed), key=lambda p: (p.sort_order, p.id))
        project_entries = [
            MenuEntry(p.display_label(), "", "enter_ticktick_project", ticktick_project_id=p.id)
            for p in open_projects
        ]
        open_project_ids = {p.id for p in open_projects}
        # Orden SIN t.completed a proposito: si entrara en la clave, completar
        # una tarea la mandaria al final del grupo y recolocaria TODAS las
        # que quedan entre medias -- justo lo que se quiere evitar (mismo
        # riesgo que ya resuelve _flat_page_builder en "Hoy" no aplica aqui,
        # porque ahi el item completado SI desaparece de la lista de una vez;
        # aqui se queda visible en gris hasta el proximo refresco real, ver
        # orchestrator.press_ticktick_toggle). Con la prioridad/id fijos, la
        # tecla de una tarea no se mueve solo por pulsarla.
        loose_tasks = sorted(
            (t for t in ticktick_tasks if t.project_id not in open_project_ids),
            key=lambda t: (-t.priority, t.id),
        )
        mixed: list[MenuEntry | TickTickTask] = [*project_entries, *loose_tasks]
        page_items, total_pages = paginate(mixed, screen.page, PAGE_SIZE)
        key_nav: dict[int, MenuEntry] = {}
        key_ticktick: dict[int, TickTickTask] = {}
        for key, item in zip(AVAILABLE_KEYS, page_items, strict=False):
            if isinstance(item, MenuEntry):
                key_nav[key] = item
            else:
                key_ticktick[key] = item
        return ResolvedPage(
            key_nav=key_nav,
            key_ticktick=key_ticktick,
            page=_clamp_page(screen.page, total_pages),
            total_pages=total_pages,
        )

    if screen.section_name:
        # screen.kind sigue siendo ScreenKind.VIEW: la seccion es un campo
        # mas de esa pantalla, no un ScreenKind propio (ver ScreenState.
        # section_name) -- view_id queda sin consultar mientras tanto.
        return _section_page(screen.section_name, habits, screen.page)

    if screen.project_name:
        # Mismo mecanismo que screen.section_name, arriba, para tareas.
        return _project_page(screen.project_name, tasks, running_timer, task_totals, screen.page)

    spec = VIEWS.get(screen.view_id) or VIEWS[DEFAULT_VIEW_ID]
    key_habit, key_task, key_template, key_timer, total_pages = spec.build_page(
        habits,
        tasks,
        templates,
        log_habits,
        timer_labels,
        running_timer,
        daily_totals,
        task_totals,
        habit_mapping,
        screen.page,
    )
    clamped_page = _clamp_page(screen.page, total_pages)
    # Atajo fijo a "Secciones"/"Proyectos" en la tecla 1 de "Habitos"/"Tareas",
    # solo en la pagina 0 -- mismo criterio que KEY_TIMER_SHORTCUT en el menu
    # (la tecla ya esta reservada en cualquier pagina via
    # _tiered_page_builder/_flat_page_builder, esto es solo lo que la rellena
    # en la primera).
    key_nav: dict[int, MenuEntry] = {}
    if clamped_page == 0:
        if spec.id == "habits":
            key_nav = {KEY_HABITS_SECTIONS_SHORTCUT: MenuEntry("Secciones", "🗂️", "open_sections")}
        elif spec.id == "tasks":
            key_nav = {KEY_TASKS_PROJECTS_SHORTCUT: MenuEntry("Proyectos", "📁", "open_projects")}
    return ResolvedPage(
        key_habit=key_habit,
        key_task=key_task,
        key_template=key_template,
        key_timer=key_timer,
        key_nav=key_nav,
        page=clamped_page,
        total_pages=total_pages,
    )


@dataclass(frozen=True)
class PressAction:
    """Que hacer tras pulsar una tecla, ya resuelta contra la pagina activa.

    Attributes:
        kind: "habit" | "habit_undo" | "habit_enter_value" | "task" |
            "template" | "timer_toggle" | "open_menu" | "open_system" |
            "select_view" | "shutdown" | "standby" | "wake" | "page_prev" |
            "page_next" | "numeric_digit" | "numeric_decimal" |
            "numeric_backspace" | "numeric_confirm" | "numeric_cancel" |
            "item_options_exit" | "task_set_priority" | "task_skip" |
            "habit_options_undo" | "habit_options_add_value" |
            "habit_options_add_step" | "section_options_exit" |
            "toggle_section_pin" | "open_projects" | "enter_project" |
            "project_options_exit" | "toggle_project_pin" |
            "open_ticktick" | "enter_ticktick_project" |
            "exit_ticktick_project" | "ticktick_toggle" | "noop".
        payload: Id del habito/tarea/plantilla/etiqueta-de-cronometro si
            ``kind`` es
            "habit"/"habit_undo"/"habit_enter_value"/"task"/"template"/
            "numeric_confirm"/"timer_toggle", el ``view_id`` destino si
            ``kind`` es "select_view", el digito tecleado si ``kind`` es
            "numeric_digit", la prioridad elegida (como texto) si ``kind`` es
            "task_set_priority", o el ``amount``/multiplicador de ``step``
            (como texto, ver ``OptionEntry.amount``) si ``kind`` es
            "habit_options_add_value"/"habit_options_add_step" -- en estos
            cuatro ultimos casos el id del habito/tarea no va aqui, se lee de
            ``ScreenState.entry_item_id`` en el momento de ejecutar, igual
            que "numeric_confirm" con ``entry_habit_id``. "habit_options_undo"
            (el "Deshacer" del menu de opciones de un habito) tampoco lleva
            payload, por el mismo motivo: el id del habito se lee de
            ``ScreenState.entry_item_id``, no de aqui -- distinto de
            "habit_undo" (el deshacer por tap corto en "Habitos"), que si
            lleva el id en el payload. "timer_toggle" es la excepcion
            deliberada a ese patron cuando se dispara desde el menu de
            opciones de una tarea (``entry.kind == "timer"``): SI lleva el id
            en el payload (leido de ``entry_item_id`` en ese momento, no
            despues), para compartir el mismo camino de ejecucion que cuando
            se pulsa directamente una tecla de "Cronometros" (que tambien
            lleva id en el payload, como "task"/"template"). "toggle_section_pin"
            tampoco lleva payload: el nombre de la seccion se lee de
            ``ScreenState.entry_section_name``, mismo patron que
            "habit_options_undo"/"task_skip". "enter_project" lleva el nombre
            del proyecto (mismo patron que "enter_section" con
            ``section_name``); "toggle_project_pin" no lleva payload (mismo
            patron que "toggle_section_pin", lee ``ScreenState.
            entry_project_name``). "enter_ticktick_project" lleva el id del
            proyecto de TickTick (mismo patron que "enter_project" con
            ``project_name``, pero con un id en vez de un nombre -- ver
            ``MenuEntry.ticktick_project_id``). "ticktick_toggle" SI lleva el
            id de la tarea de TickTick en el payload (mismo criterio que
            "task"/"timer_toggle"): quien la ejecuta relee su ``completed``
            actual para decidir completar o reabrir, en vez de decidirlo aqui
            contra una pagina que podria haberse quedado desfasada. Vacio en
            el resto.
    """

    kind: str
    payload: str = ""


def _undoes(screen: ScreenState, habit: Habit) -> bool:
    """Decide si pulsar ``habit`` en ``screen`` deshace en vez de avanzar.

    Solo deshace un habito **booleano** ya hecho hoy, y solo en una vista que
    lo declare (``ViewSpec.allows_undo``) o en una seccion (``screen.
    section_name``, ver ``ScreenState``) -- una seccion siempre deshace,
    mismo comportamiento que "Habitos", sin necesitar una entrada en
    ``VIEWS``. Un habito cuantificable nunca deshace al pulsarlo: sigue
    sumando ``step`` aunque ya haya pasado su objetivo (10/8 -> 11/8), que es
    justo lo que su tecla en gris significa.
    """
    if screen.section_name:
        allows_undo = True
    else:
        spec = VIEWS.get(screen.view_id)
        allows_undo = bool(spec and spec.allows_undo)
    return allows_undo and isinstance(habit, BooleanHabit) and habit.is_done


def _nav_press(entry: MenuEntry) -> PressAction:
    """Traduce un ``MenuEntry`` pulsado a su ``PressAction``. Compartido por
    el bloque MENU/SYSTEM/SECTIONS_MENU/PROJECTS_MENU y por el atajo fijo
    dentro de una vista de ``VIEWS`` (``KEY_HABITS_SECTIONS_SHORTCUT``/
    ``KEY_TASKS_PROJECTS_SHORTCUT``), para no repetir el mismo
    `if action == ...` varias veces."""
    if entry.action == "select_view":
        return PressAction("select_view", entry.view_id)
    if entry.action == "enter_section":
        return PressAction("enter_section", entry.section_name)
    if entry.action == "enter_project":
        return PressAction("enter_project", entry.project_name)
    if entry.action == "enter_ticktick_project":
        return PressAction("enter_ticktick_project", entry.ticktick_project_id)
    return PressAction(entry.action)


def resolve_press(screen: ScreenState, key: int, page: ResolvedPage) -> PressAction:
    """Traduce una pulsacion de tecla a una accion, contra la pagina ya
    resuelta con ``resolve_page`` para ese mismo ``screen``.

    Args:
        screen: Pantalla activa en el momento de la pulsacion.
        key: Indice de la tecla pulsada (0-14).
        page: Pagina resuelta vigente (debe venir de ``resolve_page(screen, ...)``).

    Returns:
        La accion a ejecutar por el llamador.
    """
    if screen.kind is ScreenKind.STANDBY:
        # Lo PRIMERO de todo, antes incluso que el teclado numerico: con el
        # deck a oscuras, **cualquier** tecla se limita a despertarlo y no
        # ejecuta lo que hubiera debajo. Cortocircuitar aqui, por ``kind`` y
        # sin mirar el indice de tecla, es lo que garantiza que pulsar a ciegas
        # para encender no cierre una tarea ni marque un habito por accidente.
        return PressAction("wake")

    if screen.kind is ScreenKind.NUMERIC_ENTRY:
        # Comprobacion antes que KEY_MENU/paginacion a proposito: en esta
        # pantalla las teclas 0/5/10 no significan menu/borrar-pagina-atras,
        # significan salir/borrar-caracter/confirmar (ver NUMERIC_KEYPAD).
        nk = page.key_numeric.get(key)
        if nk is None or nk.kind == "display":
            return PressAction("noop")
        if nk.kind == "cancel":
            return PressAction("numeric_cancel")
        if nk.kind == "confirm":
            return PressAction("numeric_confirm", screen.entry_habit_id)
        if nk.kind == "digit":
            return PressAction("numeric_digit", nk.label)
        if nk.kind == "decimal":
            return PressAction("numeric_decimal")
        return PressAction("numeric_backspace")

    if screen.kind is ScreenKind.ITEM_OPTIONS:
        # Misma razon que NUMERIC_ENTRY: aqui la tecla 0 no abre el menu
        # principal, vuelve a la vista de origen sin tocar el habito/tarea
        # que abrio este menu. Los mensajes informativos no hacen nada.
        entry = page.key_options.get(key)
        if entry is None or entry.kind in ("message", "blank"):
            return PressAction("noop")
        if entry.kind == "priority":
            # El id de la tarea no va en el payload (solo la prioridad
            # elegida): igual que "numeric_confirm" con entry_habit_id, el
            # llamador lo lee de screen.entry_item_id en el momento de
            # ejecutar la accion.
            return PressAction("task_set_priority", str(entry.priority))
        if entry.kind == "skip":
            # Sin payload: skip_task solo necesita el id de la tarea, que el
            # llamador lee de screen.entry_item_id, igual que arriba.
            return PressAction("task_skip")
        if entry.kind == "undo":
            # Sin payload: igual que "task_skip", el id del habito se lee de
            # screen.entry_item_id en el momento de ejecutar.
            return PressAction("habit_options_undo")
        if entry.kind == "add_value":
            # El id del habito no va en el payload (igual que "undo"): solo
            # el amount, el llamador lo lee de screen.entry_item_id.
            return PressAction("habit_options_add_value", str(entry.amount))
        if entry.kind == "add_step":
            # Mismo patron: el payload es el signo a multiplicar por el
            # step del habito, no el delta ya calculado -- eso lo hace
            # orchestrator.press_habit_options_add_step, que es quien tiene
            # el objeto Habit (con su step) a mano.
            return PressAction("habit_options_add_step", str(entry.amount))
        if entry.kind == "timer":
            # Excepcion deliberada al patron "sin id en el payload" de esta
            # pantalla: timer_toggle necesita el mismo PressAction.kind tanto
            # aqui como al pulsar una tecla de "Cronometros" (que si lleva id
            # en el payload), para compartir un unico camino de ejecucion en
            # orchestrator._run_action -- por eso el id se lee de
            # entry_item_id AQUI, no se deja para mas tarde.
            return PressAction("timer_toggle", screen.entry_item_id)
        return PressAction("item_options_exit")

    if screen.kind is ScreenKind.SECTION_OPTIONS:
        # Mismo patron que ITEM_OPTIONS justo arriba, pero con su propia
        # salida: "Volver" aqui es "section_options_exit" (regresa a
        # SECTIONS_MENU), no "item_options_exit" (regresa a VIEW).
        entry = page.key_options.get(key)
        if entry is None or entry.kind == "blank":
            return PressAction("noop")
        if entry.kind == "toggle_pin":
            # Sin payload: el nombre de la seccion se lee de
            # screen.entry_section_name en el momento de ejecutar, igual que
            # "habit_options_undo"/"task_skip" leen entry_item_id.
            return PressAction("toggle_section_pin")
        return PressAction("section_options_exit")

    if screen.kind is ScreenKind.PROJECT_OPTIONS:
        # Mismo patron que SECTION_OPTIONS justo arriba, para un proyecto:
        # "Volver" aqui es "project_options_exit" (regresa a
        # screen.entry_project_origin), no "section_options_exit".
        entry = page.key_options.get(key)
        if entry is None or entry.kind == "blank":
            return PressAction("noop")
        if entry.kind == "toggle_pin":
            return PressAction("toggle_project_pin")
        return PressAction("project_options_exit")

    if key == KEY_MENU:
        if screen.kind is ScreenKind.MENU:
            return PressAction("noop")  # ya esta en el menu, no hace falta nada
        if screen.kind is ScreenKind.TICKTICK and screen.ticktick_project_id:
            # Filtrada a un proyecto: la tecla 0 es "Volver" a la pantalla
            # principal de "TickTick" (ver resolve_page), no el menu
            # principal -- unica excepcion a "key == KEY_MENU siempre abre el
            # menu" en toda esta funcion, junto al mismo patron ya usado por
            # NUMERIC_ENTRY/ITEM_OPTIONS/SECTION_OPTIONS/PROJECT_OPTIONS
            # arriba (aunque esos cortocircuitan por screen.kind solo, este
            # necesita tambien mirar ticktick_project_id: en la pantalla
            # principal la tecla 0 sigue siendo el menu de siempre).
            return PressAction("exit_ticktick_project")
        return PressAction("open_menu")

    if key in (KEY_PAGE_PREV, KEY_PAGE_NEXT):
        if page.total_pages <= 1:
            return PressAction("noop")  # sin flecha activa, la tecla no hace nada
        return PressAction("page_prev" if key == KEY_PAGE_PREV else "page_next")

    if screen.kind in (ScreenKind.MENU, ScreenKind.SYSTEM, ScreenKind.SECTIONS_MENU, ScreenKind.PROJECTS_MENU):
        # Comprobado antes que key_nav (que en SYSTEM/SECTIONS_MENU/
        # PROJECTS_MENU siempre esta vacio, asi que aqui no cambia nada):
        # KEY_TIMER_SHORTCUT no es un MenuEntry, y el atajo generico de
        # key_nav al final de esta funcion nunca se alcanza para estos
        # cuatro kinds porque este bloque ya devuelve antes.
        shortcut = page.key_timer_shortcut.get(key)
        if shortcut is not None:
            return PressAction("timer_toggle", shortcut.id)
        entry = page.key_nav.get(key)
        return _nav_press(entry) if entry is not None else PressAction("noop")

    habit = page.key_habit.get(key)
    if habit is not None:
        if habit.manual_entry:
            # Un habito de entrada manual no tiene "deshacer": habit_set
            # sobrescribe, asi que re-teclear ya es la correccion. Siempre
            # abre el teclado, este hecho hoy o no.
            return PressAction("habit_enter_value", habit.id)
        return PressAction("habit_undo" if _undoes(screen, habit) else "habit", habit.id)
    task = page.key_task.get(key)
    if task is not None:
        return PressAction("task", task.id)
    template = page.key_template.get(key)
    if template is not None:
        # Con una ocurrencia ya abierta la tecla no crea nada: instantiate_task
        # no es idempotente y crearia un duplicado silencioso. El gris de la
        # tecla es el aviso; esto es lo que lo hace efectivo.
        if template.has_pending:
            return PressAction("noop")
        return PressAction("template", template.id)
    timer_label = page.key_timer.get(key)
    if timer_label is not None:
        return PressAction("timer_toggle", timer_label.id)
    ticktick_task = page.key_ticktick.get(key)
    if ticktick_task is not None:
        return PressAction("ticktick_toggle", ticktick_task.id)
    # Atajo fijo dentro de una vista de VIEWS (KEY_HABITS_SECTIONS_SHORTCUT en
    # "Habitos", KEY_TASKS_PROJECTS_SHORTCUT en "Tareas", ver resolve_page):
    # screen.kind aqui es ScreenKind.VIEW, no pasa por el bloque MENU/SYSTEM/
    # SECTIONS_MENU/PROJECTS_MENU de arriba, asi que necesita su propio
    # fallback a key_nav.
    nav_entry = page.key_nav.get(key)
    if nav_entry is not None:
        return _nav_press(nav_entry)
    return PressAction("noop")
