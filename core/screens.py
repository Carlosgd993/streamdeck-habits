"""Registro extensible de pantallas: menu principal, submenu "Sistema" y las
vistas de datos (Hoy/Habitos/Tareas/Crear...), todas resueltas con la misma
pareja de funciones puras.

Una pantalla resuelta en su pagina actual reparte las 15 teclas en varios
cubos -- habito, tarea, plantilla, entrada de menu o boton del teclado
numerico -- mas un total de paginas. Menu y Sistema son listas fijas de
``MenuEntry`` paginadas con ``core.key_map.paginate``; cada vista de datos se
resuelve con la funcion ``build_page`` que registra su ``ViewSpec`` en
``VIEWS``. Anadir una vista nueva es registrar una entrada mas en ``VIEWS``
(con un ``_tiered_page_builder``/``_flat_page_builder``, o uno propio) y un
boton mas en ``MENU_ENTRIES``; nada mas del sistema cambia.

Hay dos pantallas que no son menu/sistema/vista y no aparecen en ``VIEWS``:
el teclado numerico (``ScreenKind.NUMERIC_ENTRY``, ver ``NUMERIC_KEYPAD``),
que abre un habito ``manual_entry`` al pulsarlo, y el stand by
(``ScreenKind.STANDBY``), en el que el deck esta apagado y **cualquier** tecla
se limita a despertarlo.

Este modulo no sabe nada del Stream Deck (no importa nada de ``deck/``): solo
depende de ``config`` (constantes de teclas/paginacion), ``core.key_map``
(``paginate``) y ``provider.base`` (``Habit``/``Task``/``Template``), igual que
el resto de ``core/``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from config import ALL_KEYS, AVAILABLE_KEYS, KEY_MENU, KEY_PAGE_NEXT, KEY_PAGE_PREV, PAGE_SIZE
from core.key_map import paginate
from provider.base import BooleanHabit, Habit, Task, Template


class ScreenKind(Enum):
    """De que tipo es la pantalla activa."""

    MENU = auto()
    SYSTEM = auto()
    VIEW = auto()
    NUMERIC_ENTRY = auto()
    STANDBY = auto()


@dataclass
class ScreenState:
    """Pantalla activa del deck, mutable y solo en memoria.

    No se persiste nunca: cada arranque del daemon empieza en la vista por
    defecto (``DEFAULT_VIEW_ID``, "Hoy"), pagina 0.

    Attributes:
        kind: Menu principal, submenu Sistema, una vista de datos, el teclado
            numerico de entrada manual o el stand by (pantalla apagada).
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
    """

    kind: ScreenKind = ScreenKind.VIEW
    view_id: str = "today"
    page: int = 0
    entry_habit_id: str = ""
    entry_value: str = ""


@dataclass(frozen=True)
class ViewItem:
    """Un habito, una tarea o una plantilla, envuelto para poder mezclarlos en
    una sola lista paginable (p.ej. el sobrante que no cupo en el mapeo estable,
    o la lista de "Hoy" que alterna habitos y tareas)."""

    kind: str  # "habit" | "task" | "template"
    obj: Habit | Task | Template


@dataclass(frozen=True)
class MenuEntry:
    """Un boton de menu o de submenu.

    Attributes:
        label: Texto del boton.
        emoji: Icono a color, o cadena vacia.
        action: Que hace al pulsarlo -- "select_view" (entra en ``view_id``),
            "open_system" (abre el submenu Sistema), "standby" (apaga la
            pantalla del deck) o "shutdown" (apaga la Raspberry Pi). No hay
            boton de "volver": la tecla de menu ya vuelve al menu principal
            desde cualquier pantalla, incluida Sistema, asi que un boton
            "Atras" seria redundante.
        view_id: Id de la vista a la que lleva, solo si ``action`` es
            "select_view".
        key: Tecla fija dentro de la pagina 0 (p.ej. "Sistema" siempre en la
            14), o ``None`` para repartirse automaticamente entre las que
            sobren, en el orden en que aparece en la lista.
    """

    label: str
    emoji: str
    action: str
    view_id: str = ""
    key: int | None = None


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


# PageBuilder: (habitos, tareas, plantillas, mapeo_habito->tecla, pagina)
#   -> (habito_por_tecla, tarea_por_tecla, plantilla_por_tecla, total_de_paginas)
#
# Todos los builders reciben los tres conjuntos de datos aunque casi ninguno los
# use enteros: es lo que permite que una vista nueva los cruce (p.ej. "Crear"
# necesita las tareas para saber que plantillas ya tienen ocurrencia abierta).
PageBuilder = Callable[
    [list[Habit], list[Task], list[Template], dict[str, int], int],
    tuple[dict[int, Habit], dict[int, Task], dict[int, Template], int],
]

# La firma de la funcion de items que envuelve ``_flat_page_builder``.
ItemsFn = Callable[[list[Habit], list[Task], list[Template]], list[ViewItem]]


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


def _place_items(items: list[ViewItem]) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template]]:
    """Reparte ``items`` (ya recortados a una pagina) entre las teclas
    disponibles, en el orden en que llegan."""
    key_habit: dict[int, Habit] = {}
    key_task: dict[int, Task] = {}
    key_template: dict[int, Template] = {}
    for key, item in zip(AVAILABLE_KEYS, items, strict=False):
        if isinstance(item.obj, Habit):
            key_habit[key] = item.obj
        elif isinstance(item.obj, Template):
            key_template[key] = item.obj
        else:
            key_task[key] = item.obj
    return key_habit, key_task, key_template


def _overflow_items(habits: list[Habit], habit_mapping: dict[str, int]) -> list[ViewItem]:
    """Habitos que no cupieron en el mapeo estable de la pagina 0 (lo que
    antes se descartaba sin mas como ``KFUL``), en el mismo orden que usa
    ``core.key_map.update_mapping`` para asignar tecla nueva."""
    overflow_habits = sorted((h for h in habits if h.id not in habit_mapping), key=lambda h: (h.order, h.id))
    return [ViewItem("habit", h) for h in overflow_habits]


def _tiered_page_builder() -> PageBuilder:
    """Constructor de pagina para "Habitos", la unica vista que reutiliza el
    mapeo estable de habitos: la pagina 0 es literalmente el mapeo
    persistido ya calculado por ``core.key_map`` -- por eso un habito
    conserva su tecla exactamente igual entre ciclos, hecho o no. Las paginas
    siguientes son el sobrante (``_overflow_items``), sin garantia de
    estabilidad entre ciclos: es la red de seguridad, no el camino principal.
    """

    def build(
        habits: list[Habit],
        tasks: list[Task],
        templates: list[Template],
        habit_mapping: dict[str, int],
        page: int,
    ) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template], int]:
        overflow = _overflow_items(habits, habit_mapping)
        overflow_pages = paginate(overflow, 0, PAGE_SIZE)[1] if overflow else 0
        total_pages = 1 + overflow_pages
        page = max(0, min(page, total_pages - 1))

        if page == 0:
            habits_by_id = {h.id: h for h in habits}
            key_habit = {key: habits_by_id[hid] for hid, key in habit_mapping.items() if hid in habits_by_id}
            return key_habit, {}, {}, total_pages

        page_items, _ = paginate(overflow, page - 1, PAGE_SIZE)
        key_habit, key_task, key_template = _place_items(page_items)
        return key_habit, key_task, key_template, total_pages

    return build


def _flat_page_builder(items_fn: ItemsFn) -> PageBuilder:
    """Constructor de pagina generico para una vista sin reparto especial:
    pagina la lista completa que devuelva ``items_fn`` sin reservar nada para
    habitos. Es el que usa una vista nueva por defecto (p.ej. "por proyecto")
    salvo que necesite reutilizar el mapeo estable de habitos."""

    def build(
        habits: list[Habit],
        tasks: list[Task],
        templates: list[Template],
        habit_mapping: dict[str, int],
        page: int,
    ) -> tuple[dict[int, Habit], dict[int, Task], dict[int, Template], int]:
        page_items, total_pages = paginate(items_fn(habits, tasks, templates), page, PAGE_SIZE)
        key_habit, key_task, key_template = _place_items(page_items)
        return key_habit, key_task, key_template, total_pages

    return build


def _today_items(habits: list[Habit], tasks: list[Task], templates: list[Template]) -> list[ViewItem]:
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
    return [ViewItem("habit", h) for h in pending_habits] + [ViewItem("task", t) for t in tasks]


def _tasks_items(habits: list[Habit], tasks: list[Task], templates: list[Template]) -> list[ViewItem]:
    """Items de la vista "Tareas": solo las tareas pendientes, en el orden que
    ya trae el proveedor."""
    return [ViewItem("task", t) for t in tasks]


def _create_items(habits: list[Habit], tasks: list[Task], templates: list[Template]) -> list[ViewItem]:
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


VIEWS: dict[str, ViewSpec] = {
    "today": ViewSpec("today", "Hoy", "📅", _flat_page_builder(_today_items)),
    "habits": ViewSpec("habits", "Habitos", "✅", _tiered_page_builder(), allows_undo=True),
    "tasks": ViewSpec("tasks", "Tareas", "🗒️", _flat_page_builder(_tasks_items)),
    "create": ViewSpec("create", "Crear", "➕", _flat_page_builder(_create_items)),
}
DEFAULT_VIEW_ID = "today"

MENU_ENTRIES: list[MenuEntry] = [
    MenuEntry(VIEWS["today"].menu_label, VIEWS["today"].menu_emoji, "select_view", view_id="today", key=1),
    MenuEntry(VIEWS["habits"].menu_label, VIEWS["habits"].menu_emoji, "select_view", view_id="habits", key=2),
    MenuEntry(VIEWS["tasks"].menu_label, VIEWS["tasks"].menu_emoji, "select_view", view_id="tasks"),
    MenuEntry(VIEWS["create"].menu_label, VIEWS["create"].menu_emoji, "select_view", view_id="create"),
    MenuEntry("Sistema", "⚙️", "open_system", key=14),
]

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
    key_nav: dict[int, MenuEntry] = field(default_factory=dict)
    key_numeric: dict[int, NumericKey] = field(default_factory=dict)
    key_standby: dict[int, StandbyKey] = field(default_factory=dict)
    page: int = 0
    total_pages: int = 1


def _clamp_page(page: int, total_pages: int) -> int:
    return max(0, min(page, total_pages - 1))


def _nav_page(entries: list[MenuEntry], page: int) -> tuple[dict[int, MenuEntry], int]:
    """Reparte ``entries`` entre las teclas de la pagina.

    Las que traen ``key`` fijo (ver ``MenuEntry.key``) van siempre ahi, y solo
    en la pagina 0; el resto se reparte por las teclas restantes en el orden
    en que aparecen en la lista.
    """
    fixed = {entry.key: entry for entry in entries if entry.key is not None}
    auto_entries = [entry for entry in entries if entry.key is None]
    free_keys = [key for key in AVAILABLE_KEYS if key not in fixed]
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
    habit_mapping: dict[str, int],
) -> ResolvedPage:
    """Resuelve la pantalla/pagina activa contra los datos vigentes.

    Args:
        screen: Pantalla activa (menu, sistema o una vista con su pagina).
        habits: Habitos del ultimo ``get_habits()`` exitoso.
        tasks: Tareas del ultimo ``get_tasks()`` exitoso.
        templates: Plantillas del ultimo ``get_templates()`` exitoso.
        habit_mapping: Mapeo persistido habito -> tecla vigente.

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
        key_nav, total_pages = _nav_page(MENU_ENTRIES, screen.page)
        return ResolvedPage(key_nav=key_nav, page=_clamp_page(screen.page, total_pages), total_pages=total_pages)
    if screen.kind is ScreenKind.SYSTEM:
        key_nav, total_pages = _nav_page(SYSTEM_ENTRIES, screen.page)
        return ResolvedPage(key_nav=key_nav, page=_clamp_page(screen.page, total_pages), total_pages=total_pages)
    if screen.kind is ScreenKind.NUMERIC_ENTRY:
        key_numeric = dict(NUMERIC_KEYPAD)
        key_numeric[1] = NumericKey("display", screen.entry_value)
        return ResolvedPage(key_numeric=key_numeric, page=0, total_pages=1)

    spec = VIEWS.get(screen.view_id) or VIEWS[DEFAULT_VIEW_ID]
    key_habit, key_task, key_template, total_pages = spec.build_page(
        habits, tasks, templates, habit_mapping, screen.page
    )
    return ResolvedPage(
        key_habit=key_habit,
        key_task=key_task,
        key_template=key_template,
        page=_clamp_page(screen.page, total_pages),
        total_pages=total_pages,
    )


@dataclass(frozen=True)
class PressAction:
    """Que hacer tras pulsar una tecla, ya resuelta contra la pagina activa.

    Attributes:
        kind: "habit" | "habit_undo" | "habit_enter_value" | "task" |
            "template" | "open_menu" | "open_system" | "select_view" |
            "shutdown" | "standby" | "wake" | "page_prev" | "page_next" |
            "numeric_digit" | "numeric_decimal" | "numeric_backspace" |
            "numeric_confirm" | "numeric_cancel" | "noop".
        payload: Id del habito/tarea/plantilla si ``kind`` es
            "habit"/"habit_undo"/"habit_enter_value"/"task"/"template"/
            "numeric_confirm", el ``view_id`` destino si ``kind`` es
            "select_view", o el digito tecleado si ``kind`` es
            "numeric_digit". Vacio en el resto.
    """

    kind: str
    payload: str = ""


def _undoes(screen: ScreenState, habit: Habit) -> bool:
    """Decide si pulsar ``habit`` en ``screen`` deshace en vez de avanzar.

    Solo deshace un habito **booleano** ya hecho hoy, y solo en una vista que
    lo declare (``ViewSpec.allows_undo``). Un habito cuantificable nunca
    deshace al pulsarlo: sigue sumando ``step`` aunque ya haya pasado su
    objetivo (10/8 -> 11/8), que es justo lo que su tecla en gris significa.
    """
    spec = VIEWS.get(screen.view_id)
    return bool(spec and spec.allows_undo) and isinstance(habit, BooleanHabit) and habit.is_done


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

    if key == KEY_MENU:
        if screen.kind is ScreenKind.MENU:
            return PressAction("noop")  # ya esta en el menu, no hace falta nada
        return PressAction("open_menu")

    if key in (KEY_PAGE_PREV, KEY_PAGE_NEXT):
        if page.total_pages <= 1:
            return PressAction("noop")  # sin flecha activa, la tecla no hace nada
        return PressAction("page_prev" if key == KEY_PAGE_PREV else "page_next")

    if screen.kind in (ScreenKind.MENU, ScreenKind.SYSTEM):
        entry = page.key_nav.get(key)
        if entry is None:
            return PressAction("noop")
        if entry.action == "select_view":
            return PressAction("select_view", entry.view_id)
        return PressAction(entry.action)

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
    return PressAction("noop")
