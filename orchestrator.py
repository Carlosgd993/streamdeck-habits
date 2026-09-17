#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos
(con objetivo y de solo registro), las tareas pendientes, las plantillas de
creacion rapida y los cronometros (etiquetas rapidas + el que este corriendo +
el total de hoy por tarea/etiqueta + el acumulado de siempre por tarea) del
proveedor con las teclas del Stream Deck, y gestiona la navegacion por menu,
la paginacion y los pasos/deshaceres/cierres/creaciones/toggles al pulsar.

El orquestador depende solo de los puertos abstractos de ``provider.base``
(interfaces ``HabitProvider``/``TaskProvider``/``TemplateProvider``/
``TimerProvider``, modelos ``Habit``/``Task``/``Template``/``TimerLabel``/
``RunningTimer`` y excepciones ``Provider*``) y del registro de pantallas de
``core.screens``; la unica linea acoplada a un backend concreto es la
construccion del proveedor (``SupabaseProvider()``). Sustituir de API =
escribir otro adaptador que implemente esos puertos y cambiar esa linea.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import core.health as health
import core.key_map as key_map
import core.pinned_projects as pinned_projects_store
import core.pinned_sections as pinned_sections_store
import core.screens as screens
import deck.keys as deck_keys
import deck.renderer as renderer
import provider.keepalive as keepalive
from config import (
    AUTO_RETURN_SECONDS,
    LONG_PRESS_SECONDS,
    REFRESH_SECONDS,
    RESTORE_COOLDOWN_SECONDS,
    STANDBY_SECONDS,
    TIMER_SYNC_SECONDS,
    TIMER_TICK_SECONDS,
)
from core.cache import (
    ALL_RESOURCES,
    HABIT_RESOURCES,
    SUPABASE_RESOURCES,
    TASK_WRITE_RESOURCES,
    TIMER_RESOURCES,
    Resource,
    ResourceCache,
)
from core.error_codes import CODES
from deck.session import BRIGHTNESS, BRIGHTNESS_STANDBY, DeckSession
from provider.base import (
    Habit,
    HabitProvider,
    ProviderAuthError,
    ProviderError,
    RealHabit,
    RunningTimer,
    Task,
    TaskProvider,
    Template,
    TemplateProvider,
    TimerLabel,
    TimerProvider,
)
from google_tasks.base import GoogleTask, GoogleTaskList, GoogleTasksProvider
from google_tasks.client import GoogleTasksApiProvider
from provider.supabase import SupabaseProvider
from ticktick.base import TickTickProject, TickTickProvider, TickTickTask
from ticktick.client import TickTickApiProvider

state_lock = threading.Lock()
pending_requests: set[str] = set()  # ids (habito, tarea, plantilla, cronometro o el centinela de navegacion) en vuelo
_NAV_SENTINEL = "__nav__"  # clave de _claim/_release para no duplicar una entrada a vista por doble toque
_ENTRY_MAX_CHARS = 10  # limite del valor tecleado en el teclado numerico, para que quepa en el tile


def _claim(item_id: str) -> bool:
    """Reserva ``item_id`` si no tenia ya una peticion en vuelo.

    Returns:
        ``True`` si la pulsacion debe procesarse, ``False`` si hay que
        descartarla por duplicada.
    """
    with state_lock:
        if item_id in pending_requests:
            return False
        pending_requests.add(item_id)
        return True


def _release(item_id: str) -> None:
    """Libera la reserva de ``item_id`` hecha por ``_claim``."""
    with state_lock:
        pending_requests.discard(item_id)


def _safe_render(render: Callable[[], None]) -> None:
    """Ejecuta un repintado tratando cualquier fallo como error de dispositivo.

    Un deck que no responde nunca debe tumbar el hilo de callbacks ni pintarse
    a si mismo en tecla: solo se registra a fichero.
    """
    try:
        render()
    except Exception as device_exc:
        health.log_device_error(str(device_exc))


class _IdleTimer:
    """Temporizador de inactividad reiniciable: cada pulsacion lo reprograma;
    si nadie pulsa nada durante ``seconds``, dispara ``callback`` una vez.

    Hay dos instancias, con el mismo disparador (una pulsacion, cualquiera) y
    plazos distintos: ``AUTO_RETURN_SECONDS`` para volver a "Hoy" y
    ``STANDBY_SECONDS`` para entrar en stand by.

    Usa ``threading.Timer`` con su propio lock interno, independiente de
    ``screen_lock``, para que reprogramarlo (que ocurre en el hilo de
    callbacks del Stream Deck en cada pulsacion) nunca compita con un
    repintado en curso en otro hilo.
    """

    def __init__(self, seconds: float, callback: Callable[[], None]) -> None:
        self._seconds = seconds
        self._callback = callback
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def reset(self) -> None:
        """Cancela el temporizador pendiente (si lo hay) y arma uno nuevo."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._seconds, self._callback)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        """Para el temporizador pendiente sin programar uno nuevo (cierre del daemon)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def make_key_callback(
    deck: Any,
    provider: HabitProvider,
    task_provider: TaskProvider,
    template_provider: TemplateProvider,
    timer_provider: TimerProvider,
    mapping: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    log_habits_ref: dict[str, dict[str, Habit]],
    tasks_ref: dict[str, dict[str, Task]],
    templates_ref: dict[str, dict[str, Template]],
    timer_labels_ref: dict[str, dict[str, TimerLabel]],
    running_timer_ref: dict[str, RunningTimer | None],
    daily_totals_ref: dict[str, dict[str, int]],
    task_totals_ref: dict[str, dict[str, int]],
    last_timer_ref: dict[str, RunningTimer | None],
    pinned_sections: frozenset[str],
    pinned_projects: frozenset[str],
    ticktick_provider: TickTickProvider | None,
    ticktick_tasks_ref: dict[str, dict[str, TickTickTask]],
    ticktick_projects_ref: dict[str, dict[str, TickTickProject]],
    google_tasks_provider: GoogleTasksProvider | None,
    google_tasks_ref: dict[str, dict[str, GoogleTask]],
    google_lists_ref: dict[str, dict[str, GoogleTaskList]],
    screen: screens.ScreenState,
    screen_lock: threading.Lock,
    reset_idle_timers: Callable[[], None],
    dispatch_navigation: Callable[[screens.PressAction], None],
    repaint: Callable[[], None],
    invalidate: Callable[[frozenset[Resource]], None],
    refresh_after_write: Callable[[frozenset[Resource]], None],
    exit_numeric_entry: Callable[[], None],
    enter_item_options: Callable[[str, str], None],
    exit_item_options: Callable[[], None],
    enter_section_options: Callable[[str, screens.ScreenKind], None],
    enter_project_options: Callable[[str, screens.ScreenKind], None],
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para el estado actual.

    El closure resultante reprograma los temporizadores de inactividad en
    cualquier pulsacion, resuelve que tecla es (habito, tarea o accion de
    navegacion) contra la pagina vigente y actua en consecuencia:

    - **Habito**: pide al proveedor que avance un paso y, si tiene exito,
      repinta la **pantalla entera** (``repaint``, no solo esta tecla): en
      "Hoy"/"Habitos" el habito se queda en la misma tecla, solo cambia de
      blanco a gris (``is_done``, ver ``core.screens._today_items``) --
      "Completar no hace desaparecer" (ver CLAUDE.md), asi que ni un habito
      hecho ni una tarea cerrada mueven ninguna otra tecla; se recalcula toda
      la pagina igualmente porque un habito nuevo podria haber entrado por el
      otro lado del reparto. Fallo → tecla en rojo con codigo, sin tocar el
      resto. Una tecla con el objetivo ya alcanzado hoy se sigue pudiendo
      pulsar: es la base quien decide el nuevo valor (``habit_step``), y un
      habito cuantificable sigue sumando sin tope.
    - **Deshacer un habito**: la misma tecla, cuando ``core.screens``
      resuelve la pulsacion como "habit_undo" (un booleano ya hecho, en una
      vista que lo permita -- "Hoy" y "Habitos", ver
      ``core.screens.ViewSpec.allows_undo``). Pide ``undo`` al proveedor
      y, si tiene exito, repinta con el valor optimista y relee los habitos
      por detras (``refresh_after_write``): el valor que devuelve la base es el del dia y
      en un habito ``weekly_quota`` la vista pinta el contador de la semana,
      asi que el unico estado fiable es el que se relee. Fallo → tecla en rojo
      con codigo, igual que un paso.
    - **Tarea**: la pinta en verde de acuse de recibo, pide cerrarla y, solo
      cuando la base lo confirma, muta ``task.completed = True`` (se QUEDA en
      ``tasks_ref``, ver ``press_task``) y repinta la pantalla entera
      (``repaint``): la tecla pasa a gris sin moverse ni dejar hueco, mismo
      estandar que un habito. Solo el proximo refresco real la quita de la
      lista. Pulsar una tarea ya gris no hace nada (``PressAction("noop")``,
      ver ``core.screens.resolve_press``): habits-core no tiene forma de
      revertir un cierre.
    - **Plantilla** (vista "Crear"): mismo acuse verde, pide crear la
      ocurrencia y, al confirmar la base, **anade la tarea nueva a
      ``tasks_ref``** y repinta. La plantilla **no** sale de ``templates_ref``
      (a diferencia de una tarea al cerrarse): sigue en pantalla, ahora en gris
      porque ya tiene ocurrencia abierta, y su tecla deja de hacer nada --
      ``instantiate_task`` no es idempotente. Ese gris sale solo de la tarea
      insertada, ver ``core.screens._create_items``, y ``core.screens`` ya
      devuelve "noop" en ese caso, asi que aqui no hay nada que comprobar.
    - **Etiqueta de cronometro** (vista "Cronometros"), la opcion "Iniciar/
      Detener cronometro" del menu de una tarea, o el **atajo de la tecla 7
      del menu principal** (``core.screens.KEY_TIMER_SHORTCUT``, junto a
      "Cronometros" fija en la 8): las tres resuelven al mismo
      ``PressAction("timer_toggle", item_id)`` (ver
      ``core.screens.resolve_press``), asi que las ejecuta el mismo
      ``press_timer_toggle`` sea cual sea el origen. Nunca mutacion
      optimista (a diferencia de habito/tarea/plantilla): la base decide
      start-vs-stop mirando su propio estado, y puede parar un cronometro
      DISTINTO del que se pulso (el que estuviera corriendo antes) -- el
      unico estado fiable es el que trae la relectura de los cronometros
      (``refresh_after_write(TIMER_RESOURCES)``). Sin acuse verde propio: a
      diferencia de cerrar una tarea o crear desde plantilla, aqui no hay
      "peticion en vuelo" que merezca su propio color, esa relectura ya es
      casi inmediata. **No sale del menu de
      opciones de la tarea** (a diferencia de "Skip"/"cambiar prioridad"):
      se queda ahi para poder ver el cronometro corriendo o volver a
      pulsarlo para pararlo, mismo criterio que "Ajustar el progreso" de un
      habito real. Mientras haya un cronometro corriendo, dos temporizadores
      aparte (ver mas abajo) lo mantienen al dia sin necesidad de pulsar
      nada: uno repinta cada segundo (``config.TIMER_TICK_SECONDS``)
      calculando el tiempo transcurrido en el cliente a partir del
      ``started_at`` ya cacheado (sin refetch), y otro relee de verdad
      ``get_running_timer()`` cada minuto (``config.TIMER_SYNC_SECONDS``)
      para corregir esa cuenta local. La tecla 7 en concreto pinta
      ``last_timer_ref`` (el ultimo cronometro no vacio visto, corriendo o
      no -- ver ``main()``): si al pulsarla la tarea/etiqueta que recordaba
      ya no existe (completada, omitida, archivada -- carrera rara, lo
      normal es que ``_prune_stale_last_timer`` ya la haya limpiado antes de
      que la vieras), ``press_timer_toggle`` lo detecta, olvida
      ``last_timer_ref`` y repinta: la tecla vuelve sola al aviso "Sin
      cronometro" en vez de quedarse pulsable sin hacer nada.
    - **Mantener pulsado un habito o una tarea**: las tres pulsaciones de
      arriba (paso/deshacer de habito, cierre de tarea) ya no se ejecutan al
      presionar, sino al **soltar** -- ver ``on_key_change`` mas abajo. Al
      presionar se arma un temporizador de ``config.LONG_PRESS_SECONDS``; si
      se suelta antes, se cancela y se ejecuta la accion corta de siempre
      (con la duracion tipica de un toque humano de latencia, imperceptible);
      si el temporizador dispara con la tecla todavia pulsada, abre el menu
      de opciones (``ScreenKind.ITEM_OPTIONS``) para ese habito/tarea en vez
      de tocarlo, y la posterior liberacion de la tecla no hace nada (ya
      quedo consumida). El resto de teclas (plantilla, navegacion, teclado
      numerico) no tiene esta espera: siguen actuando al presionar, como
      siempre.
    - **Navegacion** (menu, submenu Sistema, cambiar de vista, paginar,
      suspender, despertar, abrir/cerrar el menu de opciones, apagar): se
      delega entera en ``dispatch_navigation``, definido en ``main()`` porque
      necesita mutar el estado de pantalla compartido.
    - **Stand by**: si la pantalla activa es ``ScreenKind.STANDBY``,
      ``core.screens.resolve_press`` devuelve "wake" para **cualquier** tecla
      antes de mirar su indice, asi que la pulsacion que enciende el deck
      nunca ejecuta lo que hubiera debajo. Aqui no hay nada que comprobar:
      llega como una accion de navegacion mas.

    Args:
        deck: El dispositivo Stream Deck.
        provider: Proveedor de habitos (puerto abstracto).
        task_provider: Proveedor de tareas (puerto abstracto).
        template_provider: Proveedor de plantillas (puerto abstracto).
        timer_provider: Proveedor de cronometros (puerto abstracto).
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
        log_habits_ref: Idem para los habitos de solo registro (``LogHabit``,
            vista "Logs"). Aparte de ``habits_ref`` porque ``get_log_habits()``
            es una lectura separada de ``get_habits()`` (ver
            ``provider.base.HabitProvider``); ``press_habit`` busca en los dos
            porque un habito pulsado puede venir de cualquiera.
        tasks_ref: Idem para las tareas pendientes.
        templates_ref: Idem para las plantillas de creacion rapida.
        timer_labels_ref: Idem para las etiquetas rapidas de cronometro
            (vista "Cronometros"). ``press_timer_toggle`` busca el id pulsado
            primero en ``tasks_ref`` (viene del menu de opciones de una
            tarea) y si no lo encuentra aqui (viene de una tecla de
            "Cronometros"), igual que ``press_habit`` mira dos ``*_ref``.
        running_timer_ref: Wrapper ``{"value": RunningTimer | None}`` (uno
            solo, no un dict por id: como mucho hay un cronometro corriendo)
            con el cronometro en marcha del ultimo ``get_running_timer()``
            exitoso. Lo usa ``on_key_change`` al resolver una pulsacion
            (``core.screens.resolve_page`` lo necesita para decidir "Iniciar"
            vs "Detener" en el menu de opciones de una tarea, y para saber si
            el atajo de la tecla 7 del menu esta corriendo ahora mismo).
        daily_totals_ref: Wrapper ``{"value": {id: segundos}}`` con los
            segundos acumulados hoy del ultimo ``get_daily_totals()`` exitoso
            (ver ``provider.base.TimerLabel.today_seconds``). Lo usa
            ``on_key_change`` al resolver una pulsacion, igual que
            ``running_timer_ref``: ``core.screens.resolve_page`` lo necesita
            para pintar el total del dia en una tecla de cronometro parada
            (vista "Cronometros" y atajo de la tecla 7).
        task_totals_ref: Wrapper ``{"value": {id: segundos}}`` con los
            segundos acumulados de SIEMPRE por tarea (sin filtrar por dia) del
            ultimo ``get_task_totals()`` exitoso (ver
            ``provider.base.Task.total_seconds``). Mismo uso que
            ``daily_totals_ref``, pero para "Hoy"/"Tareas" en vez de
            "Cronometros"/tecla 7.
        last_timer_ref: Wrapper ``{"value": RunningTimer | None}`` con el
            ultimo cronometro NO vacio visto (se actualiza en
            ``refresh_cycle``, nunca se pone a ``None`` salvo que
            ``_prune_stale_last_timer`` detecte que ya no existe): a
            diferencia de ``running_timer_ref``, sobrevive a que el
            cronometro pare. Solo lo usa ``core.screens.resolve_page`` para
            la tecla 7 del menu (``KEY_TIMER_SHORTCUT``) -- ver su docstring
            y el de ``_prune_stale_last_timer``.
        pinned_sections: Nombres de seccion (normalizados) fijados como boton
            del menu principal (``core.pinned_sections``), vigentes para este
            repintado. Se le pasa tal cual a ``core.screens.resolve_page`` en
            ``on_key_change``, igual que ``mapping``: no es un wrapper
            ``*_ref``, se recibe por valor y ya sale actualizado porque
            ``_toggle_section_pin`` siempre repinta (recreando este closure)
            justo despues de mutarlo.
        pinned_projects: Igual que ``pinned_sections``, pero para proyectos
            (``core.pinned_projects``, ``_toggle_project_pin``).
        ticktick_provider: Proveedor de TickTick (puerto ``ticktick.base.
            TickTickProvider``), o ``None`` si no se pudo inicializar (falta
            el token) -- ver ``main()``. Independiente de los cuatro
            proveedores de habits-core de arriba.
        ticktick_tasks_ref: Wrapper ``{"value": {id: TickTickTask}}`` con las
            tareas de TickTick del ultimo ``orchestrator.ticktick_refresh_cycle()``
            exitoso, mismo patron que ``tasks_ref`` pero para un ciclo de
            refresco totalmente aparte (ver ``ticktick_refresh_cycle``).
        ticktick_projects_ref: Wrapper ``{"value": {id: TickTickProject}}``
            con los proyectos de TickTick del mismo
            ``ticktick_refresh_cycle()`` exitoso que ``ticktick_tasks_ref``,
            para los botones de la pantalla principal de "TickTick" (ver
            ``core.screens.resolve_page``).
        google_tasks_provider: Proveedor de Google Tasks (puerto
            ``google_tasks.base.GoogleTasksProvider``), o ``None`` si no se
            pudo inicializar (faltan las credenciales OAuth2) -- ver
            ``main()``. Independiente de ``ticktick_provider`` y de los
            cuatro proveedores de habits-core.
        google_tasks_ref: Wrapper ``{"value": {id: GoogleTask}}`` con las
            tareas de Google Tasks del ultimo
            ``orchestrator.google_tasks_refresh_cycle()`` exitoso, mismo
            patron que ``ticktick_tasks_ref`` pero para un ciclo de refresco
            totalmente aparte.
        google_lists_ref: Wrapper ``{"value": {id: GoogleTaskList}}`` con las
            listas de Google Tasks del mismo ``google_tasks_refresh_cycle()``
            exitoso que ``google_tasks_ref``, para los botones de la pantalla
            principal de "Google Tasks" (ver ``core.screens.resolve_page``).
        screen: Pantalla activa (menu, sistema o vista con su pagina).
        screen_lock: Lock que serializa lecturas/escrituras de ``screen`` y
            ``mapping`` frente al ciclo de refresco.
        reset_idle_timers: Reprograma los dos temporizadores de inactividad
            (auto-retorno a "Hoy" y entrada en stand by). Se llama en toda
            pulsacion, sea de la tecla que sea.
        dispatch_navigation: Ejecuta cualquier ``PressAction`` que no sea de
            habito, tarea o plantilla.
        repaint: Repinta la pantalla activa entera bajo ``screen_lock``. La
            usan los pasos de habito, los cierres de tarea y las creaciones
            desde plantilla con exito, para reflejar de inmediato un cambio que
            puede desplazar otros items.
        invalidate: Marca como caducadas las lecturas que una escritura ha
            podido cambiar (``core.cache.ResourceCache.invalidate``), **sin
            pedir nada ahora**. Es lo que usa una escritura cuyo resultado
            optimista ya es exacto (un paso de habito, un cierre de tarea):
            la pantalla actual ya quedo bien, y esos datos se releeran en la
            siguiente navegacion que los necesite.
        refresh_after_write: Igual, pero ademas los relee ya, por detras
            (``orchestrator._refresh_after_write``). Para escrituras cuyo
            resultado optimista NO basta: un deshacer, un cambio de prioridad
            (que reordena) o un cronometro (que la base pudo parar en otra
            tarea). Nunca bloquea al hilo de callbacks: la relectura va en el
            hilo de refresco y repinta al terminar.
        exit_numeric_entry: Vuelve de la pantalla de teclado numerico a la
            vista de origen y repinta. Lo usa una confirmacion ("OK") con
            exito; en un fallo se queda en el teclado (ver mas abajo) para
            poder reintentar sin volver a teclear.
        enter_item_options: Abre el menu de opciones de un habito/tarea
            (``kind``, ``item_id``) sin tocar ``view_id``/``page`` y repinta.
            La dispara el temporizador de mantener pulsado (ver mas abajo),
            nunca una pulsacion normal.
        exit_item_options: Vuelve del menu de opciones a la vista de origen y
            repinta. La usan tanto "Volver" (via ``dispatch_navigation``) como
            un cambio de prioridad o un skip de tarea con exito (ver
            "task_set_priority"/"task_skip" mas abajo).
        enter_section_options: Abre la pantalla de opciones de una seccion
            (``ScreenKind.SECTION_OPTIONS``, ``section_name``, ``origin``)
            sin tocar ``screen.page`` y repinta. ``origin`` es la pantalla
            desde la que se mantuvo pulsado (``ScreenKind.SECTIONS_MENU`` o
            ``ScreenKind.MENU``, ver ``core.pinned_sections``): se guarda en
            ``ScreenState.entry_section_origin`` para que "Volver" regrese
            ahi mismo (ver mas abajo, "Mantener pulsada una seccion"). La
            dispara solo el temporizador de mantener pulsado, nunca una
            pulsacion normal.
        enter_project_options: Igual que ``enter_section_options``, pero para
            un proyecto (``ScreenKind.PROJECT_OPTIONS``, ``project_name``,
            ``ScreenState.entry_project_origin`` -- ver mas abajo, "Mantener
            pulsado un proyecto").

    Se suma un cuarto tipo de tecla, aparte de habito/tarea/plantilla:

    - **Entrada manual de un habito** (``manual_entry``, p.ej. "Peso"): pulsar
      la tecla no llama a ``step``, resuelve a ``"habit_enter_value"`` y abre
      la pantalla de teclado numerico (navegacion pura, sin red, delegada en
      ``dispatch_navigation`` igual que abrir el menu). Teclear digitos/"."/
      borrar tampoco toca la red: solo muta ``ScreenState.entry_value`` y
      repinta (tambien via ``dispatch_navigation``). Confirmar ("OK") si que
      llama al proveedor (``set_value``, aqui en ``press_habit_value``) con el
      mismo patron de acuse que un habito/tarea: si el valor tecleado esta
      vacio o no parsea como numero, no hace nada (se sigue pudiendo teclear);
      si el proveedor confirma, mutacion optimista + ``exit_numeric_entry``;
      si falla, la tecla "OK" queda en rojo con el codigo y la pantalla se
      queda en el teclado con lo tecleado intacto, para reintentar sin perder
      nada.
    - **Menu de opciones de un habito/tarea** (mantener pulsado): "Volver"
      (tecla 0 dentro de esa pantalla) resuelve a ``"item_options_exit"`` y se
      delega en ``dispatch_navigation`` -- sin red, sin tocar el habito/tarea
      que abrio el menu. Un habito abre uno de dos layouts segun su tipo (ver
      ``core.screens.resolve_page``); el de una tarea tiene dos opciones:

      - **Deshacer** (tecla 14, ambar -- en los dos layouts de habito) ->
        ``HabitProvider.undo()`` (``press_habit_undo_option``). Generico para
        cualquier ``Habit`` (con objetivo o de solo registro, ver
        ``provider.base.LogHabit``), no solo el ``BooleanHabit`` hecho que ya
        cubre el tap-undo de "Habitos" (``core.screens._undoes``). Exito ->
        mutacion optimista de ``habit.current_value`` + ``exit_item_options``
        (vuelve a la vista de origen) + relectura de los habitos por detras
        (``refresh_after_write``): el valor que devuelve la base es el
        del dia, y en un habito ``weekly_quota`` hay que releer el contador
        semanal real -- mismo motivo que el ``"habit_undo"`` de una pulsacion
        corta normal, ver ``press_habit``.
      - **Ajustar el progreso** (teclas 1-4 y 6-9, verde/granate -- solo en
        el menu de un ``RealHabit``, ver ``core.screens.REAL_HABIT_OPTIONS_LAYOUT``)
        -> ``HabitProvider.set_value()`` con ``habit.current_value + delta``,
        sin bajar de 0 (``press_habit_options_add_value``/
        ``press_habit_options_add_step``, via el helper comun
        ``_press_habit_options_delta``). "+1/+3/+5/-1/-3/-5" suman/restan un
        delta fijo (el payload de la ``PressAction`` ya lo trae); "+Paso/-Paso"
        suman/restan el ``step`` propio del habito (el payload es solo el
        signo, el delta lo calcula ``press_habit_options_add_step`` con el
        objeto ``Habit`` a mano). Exito -> mutacion optimista + relectura de
        los habitos por detras (el valor fiable es el que devuelve
        ``habit_set``), pero **a
        diferencia de "Deshacer" no sale de** ``ScreenKind.ITEM_OPTIONS``:
        se queda en la pantalla para poder encadenar varios ajustes seguidos,
        y como esa relectura repinta la pantalla activa (que sigue siendo
        esta), las teclas informativas 5/10 (progreso de hoy/unidad, ver
        ``core.screens.resolve_page``) tambien se actualizan. Solo "Volver"
        saca de aqui.
      - **Cambiar la prioridad** (teclas 11-14, blanco/verde/amarillo/rojo)
        -> ``TaskProvider.set_priority`` (``press_task_priority``). Exito ->
        mutacion optimista de ``task.priority`` + ``exit_item_options``.
      - **Skip** (tecla 1, naranja) -> mismo acuse verde que cerrar una tarea
        (``render_task_sending``), luego ``TaskProvider.skip_task``
        (``press_task_skip``). Exito -> la tarea sale de ``tasks_ref``
        (igual que al completarla) + ``exit_item_options``.
      - **Iniciar/Detener cronometro** (tecla 2, rosa/turquesa segun el
        estado) -> es la UNICA opcion de esta pantalla que SI lleva el id en
        el payload de la ``PressAction`` (``core.screens.resolve_press`` lo
        lee de ``entry_item_id`` en ese momento, ver ahi el porque): resuelve
        al mismo ``"timer_toggle"`` que una tecla de "Cronometros", asi que
        lo ejecuta ``press_timer_toggle`` por la rama de arriba, no por esta.

      Todas las demas comparten patron: sin id en el payload de la ``PressAction`` (se
      lee de ``screen.entry_item_id``), reservadas con ``_claim``/``_release``
      por ese id, y en caso de fallo la tecla queda en rojo con el codigo sin
      salir del menu, para poder reintentar.
    - **Mantener pulsada una seccion**, en las dos pantallas donde puede
      aparecer un boton de seccion -- ``ScreenKind.SECTIONS_MENU`` (ver
      ``core.screens.KEY_HABITS_SECTIONS_SHORTCUT``) y ``ScreenKind.MENU``
      (un boton ya fijado, ver ``core.pinned_sections``): al igual que un
      habito/tarea, la pulsacion tampoco se ejecuta al presionar -- se arma
      el mismo temporizador de ``LONG_PRESS_SECONDS``. Soltar antes entra en
      la seccion como de costumbre (``"enter_section"``, via
      ``dispatch_navigation`` -> ``_enter_section``) en las dos pantallas por
      igual; si dispara con la tecla aun pulsada, las dos abren
      ``ScreenKind.SECTION_OPTIONS`` para esa seccion en concreto
      (``enter_section_options``) -- **el mismo destino desde cualquiera de
      las dos pantallas**, pero cada una recuerda de donde vino
      (``on_key_change`` captura ``screen_kind`` al presionar, se guarda en
      ``ScreenState.entry_section_origin``) para que "Volver" regrese
      exactamente ahi, no siempre a "Secciones".

      La pantalla de opciones de una seccion tiene una sola opcion real:
      - **Fijar/quitar del menu principal** (tecla 14, verde/ambar segun el
        estado -- ``OptionEntry.kind == "toggle_pin"``) -> alterna
        ``core.pinned_sections`` (``_toggle_section_pin``). Es la UNICA
        escritura del deck que no llama a ningun proveedor: es un fichero
        local (``pinned_sections.json``), no puede fallar con un
        ``ProviderError``, asi que no hay tecla en rojo que contemplar aqui.
        No sale de ``SECTION_OPTIONS`` (a diferencia de "Skip"/"cambiar
        prioridad"): se queda para que la etiqueta cambie delante del usuario
        y se pueda alternar varias veces sin reabrir el menu, mismo criterio
        que "Ajustar el progreso" de un habito real. Solo "Volver" (tecla 0,
        ``"section_options_exit"``) saca de aqui, y vuelve a
        ``screen.entry_section_origin`` -- "Secciones" o el menu principal,
        segun por cual se llego (nunca a ``ScreenKind.VIEW``, a diferencia de
        "Volver" en el menu de opciones de un habito/tarea).
    - **Mantener pulsado un proyecto**, mirror exacto de "Mantener pulsada una
      seccion" para tareas: ``ScreenKind.PROJECTS_MENU`` (ver
      ``core.screens.KEY_TASKS_PROJECTS_SHORTCUT``) y ``ScreenKind.MENU`` (un
      boton ya fijado, ver ``core.pinned_projects``) arman el mismo
      temporizador; soltar antes entra en el proyecto (``"enter_project"`` ->
      ``_enter_project``); mantener pulsada abre ``ScreenKind.PROJECT_OPTIONS``
      (``enter_project_options``), con ``screen.entry_project_origin``
      guardando de donde vino, igual que ``entry_section_origin``.

      Unica opcion real: **Fijar/quitar del menu principal** (tecla 14) ->
      ``core.pinned_projects`` (``_toggle_project_pin``), mismo patron sin red
      que ``_toggle_section_pin``. Solo "Volver" (``"project_options_exit"``)
      saca de aqui, y vuelve a ``screen.entry_project_origin``.

    Returns:
        El callback ``on_key_change(deck, key, pressed)`` para el Stream Deck.
    """

    def press_habit(deck: Any, key: int, habit_id: str, *, undo: bool = False) -> None:
        # Un habito pulsado puede venir de get_habits() o de get_log_habits():
        # los ids no colisionan nunca (misma tabla, un id es de un purpose o
        # del otro, nunca de los dos), asi que basta con mirar en el que lo
        # tenga.
        habit = habits_ref["value"].get(habit_id) or log_habits_ref["value"].get(habit_id)
        if habit is None:
            return  # habito desconocido (caso defensivo entre ciclos): se ignora
        what = "Deshacer" if undo else "Paso"
        try:
            new_value = provider.undo(habit) if undo else provider.step(habit)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc), kind="habit")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"{what} FALLO [{code}]: {habit_id}", flush=True)
        else:
            # La mutacion optimista va siempre, tambien al deshacer: si el
            # refresco posterior falla, la tecla queda pintada con el estado
            # nuevo en vez de con el viejo.
            habit.current_value = new_value
            _safe_render(repaint)
            if undo:
                # El valor que devuelve la base es el del dia, y en un habito
                # weekly_quota la vista pinta el contador de la semana: hay que
                # releer de verdad. La pantalla ya quedo repintada con el valor
                # optimista, asi que la relectura va por detras (antes bloqueaba
                # el hilo de callbacks con las ocho lecturas).
                refresh_after_write(HABIT_RESOURCES)
            else:
                # Un paso si es exacto (el valor sale de la base), asi que basta
                # con marcar los habitos como caducados: se releeran en la
                # siguiente navegacion que los necesite, sin pedir nada ahora.
                invalidate(HABIT_RESOURCES)
            print(f"{what} OK: {habit.name} -> {new_value}", flush=True)

    def press_habit_value(deck: Any, key: int, habit_id: str) -> None:
        habit = habits_ref["value"].get(habit_id)
        if habit is None:
            return  # habito desconocido (caso defensivo entre ciclos): se ignora
        with screen_lock:
            typed = screen.entry_value
        try:
            value = float(typed)
        except ValueError:
            return  # vacio o invalido (p.ej. solo "."): no se envia nada, se sigue tecleando
        try:
            new_value = provider.set_value(habit, value)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc), kind="habit")
            # Se queda en el teclado (no exit_numeric_entry) para poder
            # reintentar sin perder lo tecleado; el codigo va sobre "OK".
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Entrada manual FALLO [{code}]: {habit_id}", flush=True)
        else:
            habit.current_value = new_value
            _safe_render(exit_numeric_entry)
            invalidate(HABIT_RESOURCES)  # valor exacto de la base: basta con caducarlo, sin releer ahora
            print(f"Entrada manual OK: {habit.name} -> {new_value}", flush=True)

    def _clear_running_timer_if_task(task_id: str) -> None:
        """Si ``running_timer_ref`` es justo el cronometro de ``task_id``, lo
        pone a ``None`` -- reflejando localmente lo que ``complete_task``/
        ``skip_task`` ya hicieron en el mismo commit en la base (paran
        cualquier cronometro abierto de esa tarea antes de cerrarla, ver
        ``habits-core``): sin este aviso, ``running_timer_ref`` seguiria
        diciendo "corriendo" hasta el proximo refresco real (hasta
        ``TIMER_SYNC_SECONDS`` si habia un cronometro corriendo, o
        ``REFRESH_SECONDS`` si no), y mientras tanto la tecla 7 del menu (y
        "Cronometros"/la opcion de la tarea, si siguieras viendola) seguiria
        en rosa como si el cronometro no se hubiera parado.

        Llamarlo SIEMPRE que se cierre/omita una tarea (tenga o no cronometro
        propio) es barato -- un chequeo de identidad -- y no toca nada si esa
        tarea no era la que estaba corriendo. La llaman ``press_task`` y
        ``press_task_skip``, antes del repintado optimista: el siguiente
        ``_paint_current_screen`` (via ``repaint``/``exit_item_options``) ya
        llama a ``_prune_stale_last_timer``, que con ``running_timer_ref`` a
        ``None`` puede entonces limpiar tambien ``last_timer_ref`` si la
        tarea ya no esta en ``tasks_ref`` -- la tecla 7 vuelve a "Sin
        cronometro" en el mismo repintado, no en el siguiente ciclo.
        """
        running = running_timer_ref["value"]
        if running is not None and running.task_id == task_id:
            running_timer_ref["value"] = None

    def press_task(deck: Any, key: int, task_id: str) -> None:
        """Completa ``task_id``. Aplica el estandar del proyecto para
        cualquier "check" (ver "Completar no hace desaparecer" en
        CLAUDE.md): a diferencia del comportamiento antiguo (la tarea se
        quitaba de ``tasks_ref`` y todo lo que quedaba se recolocaba), aqui
        la tarea se QUEDA en ``tasks_ref`` -- solo se muta
        ``task.completed = True`` (optimista, mismo patron que
        ``press_ticktick_toggle``) y se repinta: la tecla se pone en gris
        (``deck.renderer.render_task``) sin moverse ni dejar hueco, y
        ``core.screens.resolve_press`` ya bloquea una segunda pulsacion
        sobre ella (``PressAction("noop")``, no hay RPC de "descompletar"
        tarea en habits-core, al reves que TickTick). Solo desaparece de
        verdad en el proximo ``refresh_cycle()`` real, que reemplaza
        ``tasks_ref`` entero con lo que devuelva ``get_tasks()`` -- si sigue
        completada, ya no viene."""
        task = tasks_ref["value"].get(task_id)
        if task is None:
            return  # ya cerrada o desaparecida entre ciclos: se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            task_provider.complete_task(task)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="task")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Cierre FALLO [{code}]: {task_id}", flush=True)
        else:
            task.completed = True
            _clear_running_timer_if_task(task_id)
            _safe_render(repaint)
            # La tarea se queda en gris hasta el proximo refresco REAL (ver
            # "Completar no hace desaparecer" en CLAUDE.md), asi que no se
            # relee ahora: solo se caduca -- junto con los cronometros, porque
            # complete_task para en la base cualquiera abierto de esta tarea.
            invalidate(TASK_WRITE_RESOURCES)
            print(f"Tarea completada: {task.title}", flush=True)

    def press_ticktick_toggle(deck: Any, key: int, task_id: str) -> None:
        """Completa o reabre una tarea de la pantalla "TickTick", segun su
        estado actual (``task.completed``) -- una sola accion para las dos
        direcciones, igual que ``press_timer_toggle`` decide start-vs-stop
        por estado en vez de por dos acciones separadas.

        A diferencia de ``press_task`` (tareas de habits-core, que
        desaparecen de ``tasks_ref`` al cerrarse): aqui la tarea **se queda**
        en ``ticktick_tasks_ref`` tras completarla, solo cambia de color (gris)
        -- para poder deshacer un completado por error volviendo a pulsarla.
        Solo el proximo ``ticktick_refresh_cycle()`` (real, desde la API) la
        quita de la lista si sigue completada -- ver
        ``core.screens.ScreenKind.TICKTICK``.
        """
        if ticktick_provider is None:
            return  # sin proveedor (falta el token): no deberia haber tareas que pulsar
        task = ticktick_tasks_ref["value"].get(task_id)
        if task is None:
            return  # desaparecida entre refrescos (borrada/movida en TickTick): se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            if task.completed:
                ticktick_provider.uncomplete_task(task)
            else:
                ticktick_provider.complete_task(task)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="ticktick")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"TickTick toggle FALLO [{code}]: {task_id}", flush=True)
        else:
            task.completed = not task.completed
            _safe_render(repaint)
            invalidate(frozenset({Resource.TICKTICK}))  # igual que press_task: gris ahora, relectura al volver
            print(f"TickTick {'completada' if task.completed else 'reabierta'}: {task.title}", flush=True)

    def press_google_task_toggle(deck: Any, key: int, task_id: str) -> None:
        """Completa o reabre una tarea de la pantalla "Google Tasks", segun su
        estado actual (``task.completed``) -- mismo criterio exacto que
        ``press_ticktick_toggle``: una sola accion para las dos direcciones,
        la tarea se QUEDA en ``google_tasks_ref`` tras completarla (solo
        cambia a gris, ver "Completar no hace desaparecer" en CLAUDE.md), y
        solo el proximo ``google_tasks_refresh_cycle()`` real la quita de la
        lista si sigue completada.
        """
        if google_tasks_provider is None:
            return  # sin proveedor (faltan credenciales): no deberia haber tareas que pulsar
        task = google_tasks_ref["value"].get(task_id)
        if task is None:
            return  # desaparecida entre refrescos (borrada/movida en Google Tasks): se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            if task.completed:
                google_tasks_provider.uncomplete_task(task)
            else:
                google_tasks_provider.complete_task(task)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="google_tasks")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Google Tasks toggle FALLO [{code}]: {task_id}", flush=True)
        else:
            task.completed = not task.completed
            _safe_render(repaint)
            invalidate(frozenset({Resource.GOOGLE_TASKS}))  # igual que press_task: gris ahora, relectura al volver
            print(f"Google Tasks {'completada' if task.completed else 'reabierta'}: {task.title}", flush=True)

    def press_task_priority(deck: Any, key: int, priority_str: str) -> None:
        with screen_lock:
            task_id = screen.entry_item_id
        task = tasks_ref["value"].get(task_id)
        if task is None:
            return  # tarea desaparecida entre ciclos (cerrada por otro cliente): se ignora
        priority = int(priority_str)
        try:
            task_provider.set_priority(task, priority)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="task")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Cambiar prioridad FALLO [{code}]: {task_id}", flush=True)
        else:
            task.priority = priority
            _safe_render(exit_item_options)
            # La prioridad decide el ORDEN de la lista (priority.desc en el
            # proveedor), y eso no lo arregla la mutacion optimista: se relee
            # ya, por detras, para que la tarea aparezca donde toca.
            refresh_after_write(frozenset({Resource.TASKS}))
            print(f"Prioridad cambiada: {task.title} -> {priority}", flush=True)

    def press_task_skip(deck: Any, key: int) -> None:
        """Omite (skip) la tarea abierta en el menu de opciones.

        Deliberadamente FUERA del estandar "Completar no hace desaparecer"
        (ver CLAUDE.md, y contrastar con ``press_task`` arriba): omitir no es
        un check que se pueda pulsar por error igual de facil -- solo se
        llega aqui manteniendo pulsada la tarea y entrando en su menu de
        opciones, no con un tap normal -- asi que se mantiene el
        comportamiento de siempre: sale de ``tasks_ref`` al instante y todo
        se recoloca."""
        with screen_lock:
            task_id = screen.entry_item_id
        task = tasks_ref["value"].get(task_id)
        if task is None:
            return  # tarea desaparecida entre ciclos (cerrada/omitida por otro cliente): se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            task_provider.skip_task(task)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(task_id, str(exc), kind="task")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Skip FALLO [{code}]: {task_id}", flush=True)
        else:
            # Igual que un cierre: la tarea sale de tasks_ref para que otra
            # pulsacion no reintente omitirla, y exit_item_options repinta la
            # vista de origen ya sin ella (se recoloca sin dejar hueco).
            tasks_ref["value"].pop(task_id, None)
            _clear_running_timer_if_task(task_id)
            _safe_render(exit_item_options)
            invalidate(TASK_WRITE_RESOURCES)  # igual que completar: skip_task tambien para su cronometro
            print(f"Tarea omitida: {task.title}", flush=True)

    def press_habit_undo_option(deck: Any, key: int) -> None:
        """"Deshacer" del menu de opciones de un habito (tecla 1 de
        HABIT_OPTIONS_LAYOUT). Generaliza HabitProvider.undo() -- ya vale
        para cualquier Habit, con objetivo o de solo registro -- al menu de
        mantener pulsado, sin depender del tap-undo de "Habitos" (que solo
        cubre BooleanHabit hecho, ver core.screens._undoes).

        Igual que el "habit_undo" de una pulsacion normal (ver press_habit),
        la mutacion optimista no basta: el valor que devuelve la base es el
        del dia, y en un habito weekly_quota hay que releer el contador
        semanal real. Asi que sale del menu a la vista de origen
        (exit_item_options, que ya repinta con lo optimista) y ademas relee
        los habitos por detras (refresh_after_write) -- antes esto disparaba
        el ciclo completo, las ocho lecturas, bloqueando el hilo de teclas.
        """
        with screen_lock:
            habit_id = screen.entry_item_id
        habit = habits_ref["value"].get(habit_id) or log_habits_ref["value"].get(habit_id)
        if habit is None:
            return  # habito desaparecido entre ciclos: se ignora
        try:
            new_value = provider.undo(habit)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc), kind="habit")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Deshacer (opciones) FALLO [{code}]: {habit_id}", flush=True)
        else:
            habit.current_value = new_value
            _safe_render(exit_item_options)  # sale del menu de opciones a la vista de origen, ya repintada
            refresh_after_write(HABIT_RESOURCES)  # el valor fiable es el de la base, ver mas arriba
            print(f"Deshacer (opciones) OK: {habit.name} -> {new_value}", flush=True)

    def _press_habit_options_delta(deck: Any, key: int, amount: float, label: str) -> None:
        """Comun a los botones "+1/+3/+5/-1/-3/-5" y "+Paso/-Paso" del menu de
        opciones de un habito real (``core.screens.REAL_HABIT_OPTIONS_LAYOUT``):
        suma ``amount`` (puede ser negativo) al progreso de hoy via
        ``HabitProvider.set_value`` -- misma RPC ``habit_set`` que ya usa el
        teclado numerico de un habito ``manual_entry``, sin necesitar ninguna
        nueva -- sin bajar de 0 (la base ya hace ``greatest(valor, 0)``, pero
        clampear aqui tambien evita mandar un valor negativo que solo
        rebotaria).

        A diferencia de ``press_habit_undo_option``, aqui NO se sale de
        ``ScreenKind.ITEM_OPTIONS``: el usuario quiere poder encadenar varios
        ajustes (p.ej. "+1" tres veces) sin que cada uno lo devuelva a la
        vista de origen -- solo "Volver" saca de esta pantalla. Aun asi se
        releen los habitos por detras (``refresh_after_write``) en vez de solo
        mutar de forma optimista: el valor fiable es el que devuelve la base
        (p.ej. un habito ``weekly_quota`` pinta el contador de la semana, no
        el delta suelto que se acaba de mandar), y como ``screen.kind`` sigue
        en ``ITEM_OPTIONS``, ese repintado cae sobre esta misma pantalla -- con lo
        que las teclas informativas 5/10 (progreso de hoy/unidad, ver
        ``core.screens.resolve_page``) tambien quedan al dia.
        """
        with screen_lock:
            habit_id = screen.entry_item_id
        habit = habits_ref["value"].get(habit_id) or log_habits_ref["value"].get(habit_id)
        if habit is None:
            return  # habito desaparecido entre ciclos: se ignora
        new_value = max(0.0, habit.current_value + amount)
        try:
            confirmed = provider.set_value(habit, new_value)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(habit_id, str(exc), kind="habit")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"{label} FALLO [{code}]: {habit_id}", flush=True)
        else:
            habit.current_value = confirmed
            # No se sale de ITEM_OPTIONS (ver el docstring): se repinta esta
            # misma pantalla con el valor optimista y la relectura, que llega
            # por detras, vuelve a repintarla con el de la base.
            _safe_render(repaint)
            refresh_after_write(HABIT_RESOURCES)
            print(f"{label} OK: {habit.name} -> {confirmed}", flush=True)

    def press_habit_options_add_value(deck: Any, key: int, amount_str: str) -> None:
        """Boton "+1/+3/+5/-1/-3/-5" (``OptionEntry.kind == "add_value"``):
        el payload ya es el delta exacto a sumar, ver ``core.screens.resolve_press``."""
        _press_habit_options_delta(deck, key, float(amount_str), f"Ajuste {amount_str}")

    def press_habit_options_add_step(deck: Any, key: int, multiplier_str: str) -> None:
        """Boton "+Paso/-Paso" (``OptionEntry.kind == "add_step"``): el
        payload es el signo (``1.0``/``-1.0``) a multiplicar por el ``step``
        propio del habito -- solo un ``RealHabit`` tiene ``step``, que es el
        unico tipo que abre ``REAL_HABIT_OPTIONS_LAYOUT`` (ver
        ``core.screens.resolve_page``), asi que el ``isinstance`` de aqui es
        solo defensivo."""
        with screen_lock:
            habit_id = screen.entry_item_id
        habit = habits_ref["value"].get(habit_id) or log_habits_ref["value"].get(habit_id)
        if not isinstance(habit, RealHabit):
            return  # defensivo: este boton solo deberia llegar a un RealHabit
        multiplier = float(multiplier_str)
        label = "Paso +" if multiplier > 0 else "Paso -"
        _press_habit_options_delta(deck, key, multiplier * habit.step, label)

    def press_template(deck: Any, key: int, template_id: str) -> None:
        template = templates_ref["value"].get(template_id)
        if template is None:
            return  # plantilla desaparecida entre ciclos (desactivada, desmarcada): se ignora
        _safe_render(lambda: renderer.render_task_sending(deck, key))
        try:
            new_task_id = template_provider.create_task(template)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(template_id, str(exc), kind="template")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Crear tarea FALLO [{code}]: {template_id}", flush=True)
        else:
            # La plantilla NO se quita de templates_ref: sigue existiendo y se
            # reutiliza, solo que ahora tiene una ocurrencia abierta.
            #
            # Lo que se anade a tasks_ref es la ocurrencia nueva, no un flag en
            # la plantilla: ``core.screens._create_items`` deriva ``has_pending``
            # de las tareas en cada resolucion, asi que marcar la plantilla a
            # mano se perderia en el primer repintado. Insertando la tarea, el
            # gris sale solo -- y ademas aparece ya en "Hoy"/"Tareas" sin
            # esperar al siguiente ciclo, que es lo que el usuario espera ver.
            #
            # La base ya le puso fecha de hoy (instantiate_task sin p_due), pero
            # aqui no se conoce: ``due_day``/``overdue`` quedan en su default y
            # el proximo refresco trae la fila real. Ninguno de los dos se usa
            # para pintar.
            tasks_ref["value"][new_task_id] = Task(
                id=new_task_id,
                title=template.title,
                emoji=template.emoji,
                priority=template.priority,
                template_id=template.id,
            )
            _safe_render(repaint)
            # La ocurrencia insertada a mano no trae fecha ni orden reales
            # (los pone la base): se caduca la lista para que la primera
            # pantalla que muestre tareas la relea tal cual es.
            invalidate(frozenset({Resource.TASKS}))
            print(f"Tarea creada desde plantilla: {template.title} -> {new_task_id}", flush=True)

    def press_timer_toggle(deck: Any, key: int, item_id: str) -> None:
        """Alterna el cronometro de una tarea o de una etiqueta rapida --
        ``item_id`` sirve para las dos, asi que primero se busca en
        ``tasks_ref`` (llega aqui desde el menu de opciones de una tarea) y,
        si no esta, en ``timer_labels_ref`` (llega desde una tecla de
        "Cronometros" o del atajo de la tecla 7 del menu), mismo patron que
        ``press_habit`` mirando dos ``*_ref``.

        Nunca mutacion optimista: ``rpc/timer_toggle`` puede parar un
        cronometro DISTINTO del que se pulso (el que estuviera corriendo
        antes, si no era este), asi que el unico estado fiable es el que
        traen las lecturas de cronometro (``refresh_after_write(TIMER_RESOURCES)``).

        A diferencia de "Deshacer" o "Skip", **no sale de**
        ``ScreenKind.ITEM_OPTIONS`` cuando se pulsa desde el menu de opciones
        de una tarea: se queda ahi para poder ver el cronometro corriendo (o
        volver a pulsar para pararlo) sin tener que reabrir el menu -- mismo
        patron que "Ajustar el progreso" de un habito real
        (``_press_habit_options_delta``). Como ``screen.kind`` no cambia, esa
        relectura repinta la misma pantalla en la que se pulso (menu de
        opciones o "Cronometros"), y la tecla 2 ya sale con el label/tiempo
        al dia (``core.screens.resolve_page`` los recalcula en cada
        resolucion). Solo "Volver" saca del menu de opciones.
        """
        task = tasks_ref["value"].get(item_id)
        label = None if task is not None else timer_labels_ref["value"].get(item_id)
        if task is None and label is None:
            # No es una tarea/etiqueta viva. El caso normal es el sentinel de
            # core.screens._TIMER_SHORTCUT_EMPTY_ID (tecla 7 en "Sin
            # cronometro": no hace nada, a proposito). El caso raro es una
            # carrera con _prune_stale_last_timer: si item_id es justo lo que
            # recordaba last_timer_ref, lo olvida y repinta para que la
            # tecla 7 vuelva sola al aviso, en vez de quedarse pulsable sin
            # hacer nada -- sin llamar al proveedor, no hubo ninguna
            # mutacion que deshacer, un repintado local basta.
            last = last_timer_ref["value"]
            if last is not None and item_id and item_id in (last.task_id, last.label_id):
                last_timer_ref["value"] = None
                _safe_render(repaint)
            return
        what = task.title if task is not None else label.name
        try:
            if task is not None:
                timer_provider.toggle_task_timer(task)
            else:
                timer_provider.toggle_label_timer(label)
        except ProviderError as exc:
            _, code = health.classify(exc)
            health.log_failure(item_id, str(exc), kind="timer")
            _safe_render(lambda: renderer.render_checkin_error(deck, key, code))
            print(f"Cronometro FALLO [{code}]: {item_id}", flush=True)
        else:
            # Nunca mutacion optimista (ver el docstring): la base pudo parar
            # un cronometro DISTINTO del pulsado, asi que el unico estado
            # fiable es el que traiga la relectura -- pero solo la de
            # cronometros, no las ocho lecturas de antes.
            refresh_after_write(TIMER_RESOURCES)
            print(f"Cronometro alternado: {what}", flush=True)

    _HOLD_KINDS = ("habit", "habit_undo", "task")  # las unicas que SIEMPRE distinguen corta de mantenida
    # "enter_section"/"enter_project" se arman aparte (ver on_key_change/
    # _arm_hold): tambien distinguen corta de mantenida, pero solo cuando se
    # pulsan desde ScreenKind.SECTIONS_MENU/PROJECTS_MENU o desde un boton ya
    # fijado en ScreenKind.MENU -- las dos abren ScreenKind.SECTION_OPTIONS/
    # PROJECT_OPTIONS al mantener pulsado, solo cambia a donde regresa
    # despues "Volver" (ver ScreenState.entry_section_origin/entry_project_origin).
    _pending_hold: dict[int, tuple[screens.PressAction, threading.Timer]] = {}  # tecla -> (accion, temporizador)
    _hold_lock = threading.Lock()  # protege _pending_hold frente al hilo del temporizador

    def _run_action(deck: Any, key: int, action: screens.PressAction) -> None:
        """Ejecuta una ``PressAction`` ya resuelta (la accion corta de un
        habito/tarea, o cualquier otra que no distinga corta de mantenida)."""
        if action.kind in (
            "habit",
            "habit_undo",
            "task",
            "template",
            "numeric_confirm",
            "timer_toggle",
            "ticktick_toggle",
            "google_task_toggle",
        ):
            # Un habito reserva su id sea cual sea la operacion, asi que un
            # paso/deshacer/confirmacion de entrada manual del mismo habito
            # tampoco pueden solaparse ("numeric_confirm" lleva el habit_id
            # como payload, ver core.screens.resolve_press). "timer_toggle"
            # lleva el id de la tarea/etiqueta en el payload sea cual sea su
            # origen (tecla de "Cronometros" o menu de opciones de una tarea).
            # "ticktick_toggle"/"google_task_toggle" llevan el id de la tarea
            # de TickTick/Google Tasks, ajenos al resto (reserva de _claim
            # independiente, no colisiona con nada).
            item_id = action.payload
            if not _claim(item_id):
                return  # ya hay una peticion en vuelo para este elemento
            try:
                if action.kind == "task":
                    press_task(deck, key, item_id)
                elif action.kind == "template":
                    press_template(deck, key, item_id)
                elif action.kind == "numeric_confirm":
                    press_habit_value(deck, key, item_id)
                elif action.kind == "timer_toggle":
                    press_timer_toggle(deck, key, item_id)
                elif action.kind == "ticktick_toggle":
                    press_ticktick_toggle(deck, key, item_id)
                elif action.kind == "google_task_toggle":
                    press_google_task_toggle(deck, key, item_id)
                else:
                    press_habit(deck, key, item_id, undo=action.kind == "habit_undo")
            finally:
                _release(item_id)
        elif action.kind in (
            "task_set_priority",
            "task_skip",
            "habit_options_undo",
            "habit_options_add_value",
            "habit_options_add_step",
        ):
            # Ninguna lleva el id del habito/tarea en el payload: se lee de
            # screen.entry_item_id, igual que "numeric_confirm" lee el valor
            # tecleado de screen.entry_value.
            with screen_lock:
                item_id = screen.entry_item_id
            if not _claim(item_id):
                return  # ya hay una peticion en vuelo para este elemento
            try:
                if action.kind == "task_set_priority":
                    press_task_priority(deck, key, action.payload)
                elif action.kind == "task_skip":
                    press_task_skip(deck, key)
                elif action.kind == "habit_options_undo":
                    press_habit_undo_option(deck, key)
                elif action.kind == "habit_options_add_value":
                    press_habit_options_add_value(deck, key, action.payload)
                else:
                    press_habit_options_add_step(deck, key, action.payload)
            finally:
                _release(item_id)
        elif action.kind != "noop":
            dispatch_navigation(action)

    def _arm_hold(deck: Any, key: int, action: screens.PressAction, screen_kind: screens.ScreenKind) -> None:
        """Arma el temporizador de mantener pulsado para ``key``. Si dispara
        con la tecla todavia presionada, abre el menu de opciones en vez de
        ejecutar ``action``; si se suelta antes, ``on_key_change`` lo cancela
        y ejecuta ``action`` como una pulsacion corta normal.

        ``screen_kind`` (la pantalla en la que se pulso, capturada por
        ``on_key_change``) solo lo necesitan ``"enter_section"``/
        ``"enter_project"``: la misma accion sale tanto de una entrada de
        ``ScreenKind.SECTIONS_MENU``/``PROJECTS_MENU`` como de un boton ya
        fijado en ``ScreenKind.MENU``, y en los dos casos mantener pulsado
        abre las opciones de ESA seccion/proyecto en concreto -- pero
        "Volver" debe regresar a la pantalla de origen, distinta segun cual
        fuera (ver ``enter_section_options``/``enter_project_options``,
        ``ScreenState.entry_section_origin``/``entry_project_origin``)."""

        def _on_hold_timeout() -> None:
            with _hold_lock:
                entry = _pending_hold.pop(key, None)
            if entry is None:
                return  # ya se solto antes de que disparara (pulsacion corta): nada que hacer
            if action.kind == "enter_section":
                enter_section_options(action.payload, screen_kind)
            elif action.kind == "enter_project":
                enter_project_options(action.payload, screen_kind)
            else:
                item_kind = "habit" if action.kind in ("habit", "habit_undo") else "task"
                enter_item_options(item_kind, action.payload)

        timer = threading.Timer(LONG_PRESS_SECONDS, _on_hold_timeout)
        timer.daemon = True
        with _hold_lock:
            _pending_hold[key] = (action, timer)
        timer.start()

    def _release_hold(deck: Any, key: int) -> None:
        """Al soltar una tecla: si su temporizador de mantener pulsado seguia
        pendiente, lo cancela y ejecuta la accion corta; si ya disparo (o la
        tecla no era de las que distinguen corta/mantenida), no hace nada --
        ya se resolvio al presionar o al disparar el temporizador."""
        with _hold_lock:
            entry = _pending_hold.pop(key, None)
        if entry is None:
            return
        action, timer = entry
        timer.cancel()
        _run_action(deck, key, action)

    def on_key_change(deck: Any, key: int, pressed: bool) -> None:
        if not pressed:
            _release_hold(deck, key)
            return
        reset_idle_timers()  # cualquier pulsacion, en cualquier pantalla, reprograma auto-retorno y stand by

        with screen_lock:
            habits_list = list(habits_ref["value"].values())
            tasks_list = list(tasks_ref["value"].values())
            templates_list = list(templates_ref["value"].values())
            log_habits_list = list(log_habits_ref["value"].values())
            timer_labels_list = list(timer_labels_ref["value"].values())
            ticktick_tasks_list = list(ticktick_tasks_ref["value"].values())
            ticktick_projects_list = list(ticktick_projects_ref["value"].values())
            google_tasks_list = list(google_tasks_ref["value"].values())
            google_lists_list = list(google_lists_ref["value"].values())
            running_timer = running_timer_ref["value"]
            screen_kind = screen.kind  # capturado aqui: decide si "enter_section" arma hold, ver mas abajo
            resolved = screens.resolve_page(
                screen,
                habits_list,
                tasks_list,
                templates_list,
                log_habits_list,
                timer_labels_list,
                running_timer,
                daily_totals_ref["value"],
                task_totals_ref["value"],
                last_timer_ref["value"],
                mapping,
                pinned_sections,
                pinned_projects,
                ticktick_tasks_list,
                ticktick_projects_list,
                google_tasks_list,
                google_lists_list,
            )
            action = screens.resolve_press(screen, key, resolved)

        # "enter_section"/"enter_project" arman hold tanto desde
        # ScreenKind.SECTIONS_MENU/PROJECTS_MENU como desde un boton ya
        # fijado en ScreenKind.MENU: las dos abren las opciones de esa
        # seccion/proyecto (ver _arm_hold/enter_section_options/
        # enter_project_options) -- en cualquier otra pantalla no deberia
        # poder producirse ninguna de las dos acciones.
        is_pin_target_hold = action.kind in ("enter_section", "enter_project") and screen_kind in (
            screens.ScreenKind.SECTIONS_MENU,
            screens.ScreenKind.PROJECTS_MENU,
            screens.ScreenKind.MENU,
        )
        if action.kind in _HOLD_KINDS or is_pin_target_hold:
            _arm_hold(deck, key, action, screen_kind)
        else:
            _run_action(deck, key, action)

    return on_key_change


def main() -> None:
    """Arranca el daemon: construye el proveedor, abre el deck y corre el bucle.

    Cada iteracion ejecuta ``refresh_cycle`` y luego duerme ``REFRESH_SECONDS``.
    Cualquier excepcion ajena a los proveedores de datos se trata como error de
    dispositivo y dispara una reconexion.
    """
    try:
        provider = SupabaseProvider()  # unica linea acoplada al backend concreto
    except ProviderError as exc:
        print(f"No se pudo inicializar el proveedor de datos: {exc}", flush=True)
        sys.exit(1)

    habit_provider: HabitProvider = provider
    task_provider: TaskProvider = provider
    template_provider: TemplateProvider = provider
    timer_provider: TimerProvider = provider

    # A diferencia de SupabaseProvider() de arriba, un fallo aqui NO tumba el
    # daemon: TickTick es una capacidad anadida (PoC independiente de
    # habits-core, ver ticktick/base.py), no el nucleo del deck. Sin token
    # configurado, ticktick_provider queda en None y la pantalla "TickTick"
    # pinta directamente el codigo AUTH sin intentar red (ver
    # ticktick_refresh_cycle).
    ticktick_provider: TickTickProvider | None
    try:
        ticktick_provider = TickTickApiProvider()
    except ProviderError as exc:
        print(f"TickTick no disponible (pantalla 'TickTick' pintara AUTH): {exc}", flush=True)
        ticktick_provider = None

    # Mismo criterio que ticktick_provider justo arriba: un fallo aqui NO
    # tumba el daemon, es otra capacidad anadida (ver google_tasks/base.py).
    google_tasks_provider: GoogleTasksProvider | None
    try:
        google_tasks_provider = GoogleTasksApiProvider()
    except ProviderError as exc:
        print(f"Google Tasks no disponible (pantalla 'Google Tasks' pintara AUTH): {exc}", flush=True)
        google_tasks_provider = None

    session = DeckSession()
    session.open()

    mapping = key_map.load_map()
    # Nombres de seccion (normalizados) fijados como boton del menu principal
    # (ver core.pinned_sections/ScreenKind.SECTION_OPTIONS). Igual que mapping:
    # variable simple, no un wrapper *_ref, mutada por nonlocal en _toggle_section_pin.
    pinned_sections: frozenset[str] = pinned_sections_store.load()
    # Idem para proyectos (ver core.pinned_projects/ScreenKind.PROJECT_OPTIONS),
    # mutada por nonlocal en _toggle_project_pin.
    pinned_projects: frozenset[str] = pinned_projects_store.load()
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    log_habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # idem para los LogHabit de get_log_habits()
    tasks_ref: dict[str, dict[str, Task]] = {"value": {}}  # task_id -> objeto Task, actualizado cada ciclo
    templates_ref: dict[str, dict[str, Template]] = {"value": {}}  # template_id -> Template, idem
    timer_labels_ref: dict[str, dict[str, TimerLabel]] = {"value": {}}  # label_id -> TimerLabel, idem
    running_timer_ref: dict[str, RunningTimer | None] = {"value": None}  # uno solo, no por id: a lo sumo uno corre
    daily_totals_ref: dict[str, dict[str, int]] = {"value": {}}  # id tarea/etiqueta -> segundos acumulados hoy
    task_totals_ref: dict[str, dict[str, int]] = {"value": {}}  # task_id -> segundos acumulados de siempre
    # Ultimo running_timer_ref["value"] NO vacio visto (atajo tecla 7 del
    # menu, ver core.screens.KEY_TIMER_SHORTCUT): se actualiza junto con
    # running_timer_ref en refresh_cycle, pero nunca se pone a None solo
    # porque el cronometro pare -- solo _prune_stale_last_timer lo hace, y
    # solo si la tarea/etiqueta que recordaba ya no existe. Arranca vacio: el
    # daemon no sabe que corria antes de este arranque.
    last_timer_ref: dict[str, RunningTimer | None] = {"value": None}
    # Tareas de TickTick del ultimo ticktick_refresh_cycle() exitoso, mismo
    # patron que tasks_ref pero para un ciclo de refresco totalmente aparte
    # (ver ticktick_refresh_cycle) -- nunca lo toca refresh_cycle().
    ticktick_tasks_ref: dict[str, dict[str, TickTickTask]] = {"value": {}}
    # Proyectos de TickTick del mismo ticktick_refresh_cycle() exitoso, para
    # los botones de la pantalla principal de "TickTick" (ver
    # core.screens.resolve_page).
    ticktick_projects_ref: dict[str, dict[str, TickTickProject]] = {"value": {}}
    # Tareas de Google Tasks del ultimo google_tasks_refresh_cycle() exitoso,
    # mismo patron que ticktick_tasks_ref pero para un ciclo de refresco
    # totalmente aparte (ver google_tasks_refresh_cycle).
    google_tasks_ref: dict[str, dict[str, GoogleTask]] = {"value": {}}
    # Listas de Google Tasks del mismo google_tasks_refresh_cycle() exitoso,
    # para los botones de la pantalla principal de "Google Tasks" (ver
    # core.screens.resolve_page).
    google_lists_ref: dict[str, dict[str, GoogleTaskList]] = {"value": {}}

    screen = screens.ScreenState()  # arranca en "Hoy", pagina 0
    screen_lock = threading.Lock()  # serializa screen/mapping entre el ciclo y los callbacks
    # Cuando se leyo cada cosa por ultima vez y con que resultado (ver
    # core.cache): sustituye a los nueve "last_*_code" sueltos que habia antes
    # -- ahora el codigo de error de cada lectura vive junto a su caducidad,
    # que es lo que decide si hay que volver a pedirla.
    cache = ResourceCache()
    # Un solo hilo para TODAS las relecturas en segundo plano: el hilo de
    # callbacks del deck nunca debe esperar a la red (era justo lo que hacia
    # que pulsar una tecla no respondiera mientras se refrescaba). Uno basta
    # y ademas serializa las peticiones, que es lo que quiere una Pi 3.
    fetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refresco")
    # time.monotonic() del ultimo intento de reactivar el proyecto (ver _maybe_restore_project)
    last_restore_attempt = 0.0

    def _prune_stale_last_timer() -> None:
        """Si ``last_timer_ref`` recuerda una tarea ya completada/omitida o
        una etiqueta ya archivada (o borrada), la olvida: la tecla 7 del
        menu vuelve sola al aviso "Sin cronometro" en el siguiente
        repintado, sin esperar a que alguien la pulse (ver
        ``core.screens.KEY_TIMER_SHORTCUT``/``_timer_shortcut_item``).

        Se llama al PRINCIPIO de todo repintado (``_paint_current_screen``),
        no solo dentro de ``refresh_cycle``: completar/omitir una tarea desde
        el propio deck (``press_task``/``press_task_skip``) solo repinta con
        mutacion optimista local, sin pasar por ``refresh_cycle`` -- si esta
        comprobacion viviera solo alli, la tecla 7 podria seguir enseñando el
        titulo de una tarea ya cerrada hasta el proximo refresco (hasta 15
        min). Aqui se comprueba contra lo que haya en ``tasks_ref``/
        ``timer_labels_ref`` EN ESE MOMENTO, frescos o no -- si la ultima
        lectura fallo y se conserva la anterior, como mucho se retrasa la
        limpieza al proximo ciclo que si tenga exito, nunca se limpia de mas.

        No toca nada si lo recordado es justo lo que esta corriendo ahora
        (comparacion por identidad, ver ``refresh_cycle``): eso lo decide
        siempre ``running_timer_ref``, nunca esta poda.

        Una tarea completada (``Task.completed``) cuenta como "ya no
        existe" aqui aunque siga en ``tasks_ref`` (ver "Completar no hace
        desaparecer" en CLAUDE.md: ya no se quita al cerrarla, solo se pinta
        en gris) -- sin este chequeo extra la tecla 7 seguiria enseñando el
        recordatorio de una tarea ya cerrada hasta el proximo refresco real,
        en vez de volver de inmediato al aviso "Sin cronometro".
        """
        last = last_timer_ref["value"]
        if last is None or last is running_timer_ref["value"]:
            return
        if last.task_id:
            task = tasks_ref["value"].get(last.task_id)
            if task is None or task.completed:
                last_timer_ref["value"] = None
        elif last.label_id and last.label_id not in timer_labels_ref["value"]:
            last_timer_ref["value"] = None

    def _paint_current_screen() -> None:
        """Resuelve la pantalla activa contra los datos vigentes, la pinta y
        re-registra el callback de tecla.

        PRECONDICION: se llama siempre con ``screen_lock`` ya adquirido por
        el llamador (nunca lo adquiere el mismo).
        """
        _prune_stale_last_timer()
        deck = session.deck
        resolved = screens.resolve_page(
            screen,
            list(habits_ref["value"].values()),
            list(tasks_ref["value"].values()),
            list(templates_ref["value"].values()),
            list(log_habits_ref["value"].values()),
            list(timer_labels_ref["value"].values()),
            running_timer_ref["value"],
            daily_totals_ref["value"],
            task_totals_ref["value"],
            last_timer_ref["value"],
            mapping,
            pinned_sections,
            pinned_projects,
            list(ticktick_tasks_ref["value"].values()),
            list(ticktick_projects_ref["value"].values()),
            list(google_tasks_ref["value"].values()),
            list(google_lists_ref["value"].values()),
        )
        _safe_render(lambda: renderer.render_page(deck, resolved))

        # Los codigos de error se pintan DESPUES del repintado general, para
        # que tapen los datos viejos de la parte que fallo, y solo si la
        # pantalla visible los usa (un fallo de tareas no debe teñir "Sistema").
        # Los ids de vista van literales a proposito: es el unico sitio fuera de
        # core/screens.py que los conoce, y una vista nueva tiene que decidir
        # explicitamente que codigos le afectan.
        # Ultimo codigo de cada lectura (core.cache): la que fue bien trae
        # None, la que fallo trae su codigo corto hasta que vuelva a ir bien.
        habits_code = cache.code(Resource.HABITS)
        log_habits_code = cache.code(Resource.LOG_HABITS)
        tasks_code = cache.code(Resource.TASKS)
        templates_code = cache.code(Resource.TEMPLATES)
        timer_labels_code = cache.code(Resource.TIMER_LABELS)
        ticktick_code = cache.code(Resource.TICKTICK)
        google_tasks_code = cache.code(Resource.GOOGLE_TASKS)

        is_view = screen.kind is screens.ScreenKind.VIEW
        # Una seccion (screen.section_name) tambien depende de get_habits(),
        # igual que "Hoy"/"Habitos" -- ver core.screens.ScreenState.section_name.
        # Un proyecto (screen.project_name) es el equivalente para get_tasks().
        # Mientras hay seccion/proyecto activo, view_id queda con lo que
        # hubiera antes de entrar (no se toca, ver orchestrator._enter_section/
        # _enter_project) y NO debe consultarse para las comprobaciones que no
        # le tocan: is_plain_view los deja fuera, para no pintar por error un
        # codigo de logs/plantillas/cronometros (ni tareas sobre una seccion,
        # ni habitos sobre un proyecto) sobre una pantalla que no tiene nada
        # de eso.
        is_plain_view = is_view and not screen.section_name and not screen.project_name
        if habits_code is not None and is_view and (screen.section_name or screen.view_id in ("today", "habits")):
            code = habits_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_habit.keys(), code))
        if log_habits_code is not None and is_plain_view and screen.view_id == "logs":
            code = log_habits_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_habit.keys(), code))
        if tasks_code is not None and is_view and (
            screen.project_name or (is_plain_view and screen.view_id in ("today", "tasks"))
        ):
            code = tasks_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_task.keys(), code))
        if templates_code is not None and is_plain_view and screen.view_id == "create":
            code = templates_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_template.keys(), code))
        if timer_labels_code is not None and is_plain_view and screen.view_id == "timers":
            # Solo el catalogo de etiquetas pinta rojo: sin el no hay nada que
            # ofrecer. Un fallo de Resource.RUNNING_TIMER NO se pinta aqui a
            # proposito: solo se pierde el resaltado de "cual esta corriendo",
            # y el toggle sigue siendo correcto porque lo decide
            # rpc/timer_toggle contra el estado real, no contra lo cacheado.
            code = timer_labels_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_timer.keys(), code))
        if ticktick_code is not None and screen.kind is screens.ScreenKind.TICKTICK:
            # Por screen.kind, no por view_id: TickTick no es una VIEW (ver
            # core.screens.ScreenKind.TICKTICK), asi que is_view/is_plain_view
            # de arriba no le pegan. Incluye resolved.key_nav: en la pantalla
            # principal son los botones de proyecto (ver core.screens.
            # resolve_page), que dependen del mismo ticktick_refresh_cycle()
            # que las tareas -- en la pantalla filtrada por proyecto siempre
            # esta vacio, asi que aqui no cambia nada.
            code = ticktick_code
            keys = list(resolved.key_ticktick.keys()) + list(resolved.key_nav.keys())
            _safe_render(lambda: renderer.render_error_all(deck, keys, code))
        if google_tasks_code is not None and screen.kind is screens.ScreenKind.GOOGLE_TASKS:
            # Mismo criterio que TickTick justo arriba, para Google Tasks.
            code = google_tasks_code
            keys = list(resolved.key_google_tasks.keys()) + list(resolved.key_nav.keys())
            _safe_render(lambda: renderer.render_error_all(deck, keys, code))

        deck.set_key_callback(
            make_key_callback(
                deck,
                habit_provider,
                task_provider,
                template_provider,
                timer_provider,
                mapping,
                habits_ref,
                log_habits_ref,
                tasks_ref,
                templates_ref,
                timer_labels_ref,
                running_timer_ref,
                daily_totals_ref,
                task_totals_ref,
                last_timer_ref,
                pinned_sections,
                pinned_projects,
                ticktick_provider,
                ticktick_tasks_ref,
                ticktick_projects_ref,
                google_tasks_provider,
                google_tasks_ref,
                google_lists_ref,
                screen,
                screen_lock,
                _reset_idle_timers,
                _dispatch_navigation,
                _repaint_locked,
                cache.invalidate,
                _refresh_after_write,
                _exit_numeric_entry,
                _enter_item_options,
                _exit_item_options,
                _enter_section_options,
                _enter_project_options,
            )
        )

    def _repaint_locked() -> None:
        """Repinta la pantalla activa adquiriendo ``screen_lock`` (a
        diferencia de ``_paint_current_screen``, que asume el lock ya
        adquirido). La usa ``make_key_callback`` tras un paso de habito o un
        cierre de tarea con exito, fuera de cualquier ``with screen_lock``
        en curso, para reflejar de inmediato el cambio de color de esa
        tecla (blanco/prioridad -> gris, ver "Completar no hace desaparecer"
        en CLAUDE.md) -- se repinta la pantalla entera, no solo la tecla,
        por simetria con el resto de acciones optimistas de este modulo."""
        with screen_lock:
            _paint_current_screen()

    def _maybe_restore_project() -> None:
        """Si alguna lectura de habits-core arrastra NET, intenta reactivar el
        proyecto Supabase activo (ver
        ``provider.keepalive``): un NET persistente es el sintoma de un
        proyecto pausado por inactividad tanto como el de "sin red", y no
        hay forma de distinguirlos desde el propio checkin, asi que se
        intenta siempre que se vea NET -- sin token configurado,
        ``try_restore_active_project`` no hace nada (ver su docstring).

        Se auto-limita a un intento cada ``RESTORE_COOLDOWN_SECONDS``:
        reactivar tarda uno o dos minutos, y NET se repite cada ciclo
        mientras tanto. Se llama SIN ``screen_lock`` (ver ``_fetch_resources``)
        y lanza la peticion en su propio hilo para no retrasar el repintado
        ni una pulsacion en curso con una llamada de red que no las afecta.

        Solo cuentan las lecturas de habits-core (``SUPABASE_RESOURCES``): un
        NET de la API de TickTick o de Google no dice nada del proyecto
        Supabase.
        """
        nonlocal last_restore_attempt
        if not cache.has_code("NET", SUPABASE_RESOURCES):
            return
        now = time.monotonic()
        if now - last_restore_attempt < RESTORE_COOLDOWN_SECONDS:
            return
        last_restore_attempt = now

        def _attempt() -> None:
            ok = keepalive.try_restore_active_project()
            print(f"Reactivacion de proyecto Supabase {'solicitada' if ok else 'no disponible'}", flush=True)

        threading.Thread(target=_attempt, daemon=True).start()

    # --- Lecturas: que se pide, como se aplica, y cuando se vuelve a pedir ---
    #
    # Una entrada por Resource (ver core.cache): la funcion que la lee, como
    # se aplica su resultado y con que nombre sale en el log. Antes esto eran
    # ocho bloques try/except copiados dentro de refresh_cycle, siempre los
    # ocho; en forma de tabla, pedir solo un subconjunto -- lo que necesita la
    # pantalla activa, o lo que acaba de tocar una escritura -- sale gratis.
    def _fetch_ticktick() -> tuple[list[TickTickTask], list[TickTickProject]]:
        """Las dos lecturas de TickTick como una sola (tareas + proyectos de
        los botones): mismo proveedor, misma pantalla y un unico codigo de
        error, igual que cuando ``ticktick_refresh_cycle`` las pedia a mano.

        Sin token configurado (``ticktick_provider`` es ``None``, ver
        ``main``) ni siquiera intenta red: AUTH directo, el mismo codigo que
        pintaria un 401 real."""
        if ticktick_provider is None:
            raise ProviderAuthError("Falta TICKTICK_ACCESS_TOKEN en el .env")
        return ticktick_provider.get_tasks(), ticktick_provider.get_projects()

    def _fetch_google_tasks() -> tuple[list[GoogleTask], list[GoogleTaskList]]:
        """Las lecturas de Google Tasks como una sola, mismo criterio que
        ``_fetch_ticktick``: primero las listas (para saber cuales pedir) y
        luego, con esos ids, las tareas de cada una -- el "1+N" que exige la
        API de Google Tasks (ver ``google_tasks.base.GoogleTasksProvider``),
        bajo un unico codigo de error.

        Sin credenciales configuradas (``google_tasks_provider`` es
        ``None``, ver ``main``) ni siquiera intenta red: AUTH directo."""
        if google_tasks_provider is None:
            raise ProviderAuthError("Falta GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REFRESH_TOKEN en el .env")
        google_lists = google_tasks_provider.get_task_lists()
        google_tasks = google_tasks_provider.get_tasks([lst.id for lst in google_lists])
        return google_tasks, google_lists

    fetchers: dict[Resource, Callable[[], Any]] = {
        Resource.HABITS: habit_provider.get_habits,
        Resource.LOG_HABITS: habit_provider.get_log_habits,
        Resource.TASKS: task_provider.get_tasks,
        Resource.TEMPLATES: template_provider.get_templates,
        Resource.TIMER_LABELS: timer_provider.get_timer_labels,
        Resource.RUNNING_TIMER: timer_provider.get_running_timer,
        Resource.DAILY_TOTALS: timer_provider.get_daily_totals,
        Resource.TASK_TOTALS: timer_provider.get_task_totals,
        Resource.TICKTICK: _fetch_ticktick,
        Resource.GOOGLE_TASKS: _fetch_google_tasks,
    }
    # Como se llama cada lectura en el log, con el mismo texto que ya usaba
    # cada bloque del ciclo antiguo (para no romper la lectura del journal).
    fetch_labels: dict[Resource, str] = {
        Resource.HABITS: "habitos",
        Resource.LOG_HABITS: "logs",
        Resource.TASKS: "tareas",
        Resource.TEMPLATES: "plantillas",
        Resource.TIMER_LABELS: "cronometros",
        Resource.RUNNING_TIMER: "cronometro activo",
        Resource.DAILY_TOTALS: "totales de hoy",
        Resource.TASK_TOTALS: "totales de siempre",
        Resource.TICKTICK: "ticktick",
        Resource.GOOGLE_TASKS: "google_tasks",
    }
    # Orden fijo en que se piden, cuando se piden varias: el mismo de siempre,
    # para que el journal se lea igual que antes.
    fetch_order: tuple[Resource, ...] = (
        Resource.HABITS,
        Resource.LOG_HABITS,
        Resource.TASKS,
        Resource.TEMPLATES,
        Resource.TIMER_LABELS,
        Resource.RUNNING_TIMER,
        Resource.DAILY_TOTALS,
        Resource.TASK_TOTALS,
        Resource.TICKTICK,
        Resource.GOOGLE_TASKS,
    )

    def _apply(resource: Resource, value: Any) -> None:
        """Vuelca al estado compartido el resultado de una lectura con exito.

        PRECONDICION: ``screen_lock`` ya adquirido (lo hace
        ``_fetch_resources``). Cada rama es exactamente lo que hacia el bloque
        equivalente del ciclo antiguo, incluido que solo los habitos tocan el
        mapeo persistido de teclas -- llamar a ``key_map.update_mapping`` tras
        una lectura fallida liberaria las teclas de habitos que siguen
        existiendo, por eso solo se llega aqui con exito.
        """
        nonlocal mapping
        if resource is Resource.HABITS:
            mapping = key_map.update_mapping(
                value, mapping, reserved_keys=frozenset({screens.KEY_HABITS_SECTIONS_SHORTCUT})
            )
            habits_ref["value"] = {h.id: h for h in value}
        elif resource is Resource.LOG_HABITS:
            log_habits_ref["value"] = {h.id: h for h in value}
        elif resource is Resource.TASKS:
            tasks_ref["value"] = {t.id: t for t in value}
        elif resource is Resource.TEMPLATES:
            templates_ref["value"] = {t.id: t for t in value}
        elif resource is Resource.TIMER_LABELS:
            timer_labels_ref["value"] = {tl.id: tl for tl in value}
        elif resource is Resource.RUNNING_TIMER:
            running_timer_ref["value"] = value
            if value is not None:
                # last_timer_ref se queda con el ultimo valor NO vacio: nunca
                # se pisa con None solo porque el cronometro pare (eso es lo
                # que permite el atajo de la tecla 7, ver
                # core.screens.KEY_TIMER_SHORTCUT). Solo lo limpia
                # _prune_stale_last_timer, en _paint_current_screen.
                last_timer_ref["value"] = value
        elif resource is Resource.DAILY_TOTALS:
            daily_totals_ref["value"] = value
        elif resource is Resource.TASK_TOTALS:
            task_totals_ref["value"] = value
        elif resource is Resource.TICKTICK:
            ticktick_tasks, ticktick_projects = value
            ticktick_tasks_ref["value"] = {t.id: t for t in ticktick_tasks}
            ticktick_projects_ref["value"] = {p.id: p for p in ticktick_projects}
        else:
            google_tasks, google_lists = value
            google_tasks_ref["value"] = {t.id: t for t in google_tasks}
            google_lists_ref["value"] = {lst.id: lst for lst in google_lists}

    def _fetch_resources(resources: frozenset[Resource]) -> None:
        """Lee ``resources``, aplica lo que llegue y repinta la pantalla activa.

        Sustituye al ciclo antiguo de ocho lecturas fijas: ahora el llamador
        dice QUE quiere -- la pantalla activa pide lo suyo
        (``_revalidate_screen``), una escritura pide lo que ha tocado
        (``_refresh_after_write``), el ciclo periodico lo pide todo
        (``refresh_cycle``).

        Conserva las dos propiedades del ciclo antiguo:

        - **La red va SIN ``screen_lock``.** Cada peticion puede tardar hasta
          10s (timeout de ``provider/supabase.py``) antes de fallar, y
          ``on_key_change`` necesita ese mismo lock para resolver una
          pulsacion: sujetarlo mientras dura la red dejaba el deck entero sin
          responder, cada minuto, mientras un cronometro corria (se detecto en
          produccion). Solo aplicar resultados y repintar -- sincrono, sin red
          -- lo sujeta.
        - **Cada lectura falla por separado**: la que falla conserva los datos
          de la anterior, guarda su codigo y no afecta a las demas.

        Y anade las dos garantias de ``core.cache``: no se pide lo que ya se
        esta pidiendo (``begin_fetch`` devuelve ``None``), y un resultado que
        quedo obsoleto por una escritura hecha mientras estaba en vuelo se
        descarta y se vuelve a pedir (``end_fetch``), en vez de resucitar en
        pantalla lo que el usuario acaba de cambiar.
        """
        results: list[tuple[Resource, int, Any, str | None]] = []
        network_started = time.monotonic()
        for resource in fetch_order:
            if resource not in resources:
                continue
            token = cache.begin_fetch(resource)
            if token is None:
                continue  # ya hay una lectura de esto en vuelo: la suya repinta por las dos
            value: Any = None
            code: str | None = None
            try:
                value = fetchers[resource]()
            except ProviderError as exc:
                _, code = health.classify(exc)
                print(f"[{code}] {CODES[code]} ({fetch_labels[resource]}): {exc}", flush=True)
            results.append((resource, token, value, code))
        if not results:
            return
        network_ms = (time.monotonic() - network_started) * 1000

        obsolete: set[Resource] = set()
        applied: list[Resource] = []
        paint_started = time.monotonic()
        with screen_lock:
            for resource, token, value, code in results:
                if not cache.end_fetch(resource, token, code=code):
                    obsolete.add(resource)
                    continue
                if code is None:
                    _apply(resource, value)
                applied.append(resource)
            if applied:
                # Se repinta aunque solo llegaran fallos: sus codigos tienen
                # que salir en tecla, igual que en el ciclo antiguo.
                _paint_current_screen()
        paint_ms = (time.monotonic() - paint_started) * 1000

        if applied:
            # Una linea por lectura real. Su AUSENCIA es la otra mitad de la
            # informacion: navegar sin que aparezca nada aqui significa que la
            # cache sirvio los datos y no se toco la red.
            names = ",".join(sorted(resource.value for resource in applied))
            print(f"Lectura [{names}] red {network_ms:.0f} ms, pintado {paint_ms:.0f} ms", flush=True)
        if obsolete:
            _revalidate_async(frozenset(obsolete))
        _maybe_restore_project()  # fuera de screen_lock: es red, no toca pantalla ni mapeo

    def _revalidate_async(resources: frozenset[Resource]) -> None:
        """Lee ``resources`` en el hilo de refresco, sin bloquear al llamador.

        Es el camino de todo refresco disparado por una pulsacion: la pantalla
        ya se pinto con lo cacheado, asi que el hilo de callbacks del deck
        queda libre para atender la siguiente tecla mientras la red va por
        detras. Un fallo inesperado aqui no puede matar ese hilo: se registra
        como error de dispositivo, igual que en el bucle principal.
        """
        if not resources:
            return

        def _run() -> None:
            try:
                _fetch_resources(resources)
            except Exception as exc:
                health.log_device_error(str(exc))
                print(f"Error en el refresco en segundo plano: {exc}", flush=True)

        fetch_executor.submit(_run)

    def _revalidate_screen() -> None:
        """Pide lo que la pantalla activa necesite Y tenga caducado.

        El "pintar ya, refrescar detras" en una linea: el llamador ya pinto
        con la cache, y esto solo toca la red si hace falta de verdad. Si esos
        datos se leyeron hace menos de ``CACHE_TTL_SECONDS`` no se pide nada,
        que es justo lo que hace gratis el ir y volver entre pantallas.
        """
        with screen_lock:
            needs = screens.needs_for(screen)
            max_age = screens.max_age_for(screen)
        _revalidate_async(cache.stale(needs, max_age))

    def _refresh_after_write(resources: frozenset[Resource]) -> None:
        """Invalida ``resources`` y los relee ya, tras una escritura cuyo
        resultado optimista no basta: un deshacer (el valor fiable es el de la
        base, que en un habito ``weekly_quota`` es el contador semanal), un
        cambio de prioridad (que reordena la lista), o un cronometro (que la
        base pudo parar en otra tarea distinta de la pulsada).

        La invalidacion va ANTES de pedir, a proposito: asi una lectura que
        siguiera en vuelo desde antes de la escritura se descarta al volver
        (``core.cache.ResourceCache.end_fetch``) en vez de pintar el estado
        viejo encima del que el usuario acaba de provocar.
        """
        cache.invalidate(resources)
        _revalidate_async(resources)

    def refresh_cycle() -> None:
        """Relee TODO lo de habits-core y repinta.

        Es el ciclo periodico de ``REFRESH_SECONDS`` y la red de seguridad de
        la cache: lo que no se haya refrescado por navegar o por escribir cae
        aqui de todas formas.
        """
        _fetch_resources(SUPABASE_RESOURCES)

    def ticktick_refresh_cycle() -> None:
        """Relee TickTick y repinta: sigue siendo un ciclo aparte del de
        habits-core (PoC independiente, ver ``ticktick/base.py``), ahora como
        una lectura mas de la tabla -- ``Resource.TICKTICK``, con su propio
        proveedor y su propio codigo de error."""
        _fetch_resources(frozenset({Resource.TICKTICK}))

    def google_tasks_refresh_cycle() -> None:
        """Relee Google Tasks y repinta: mismo patron exacto que
        ``ticktick_refresh_cycle``, otro ciclo aparte del de habits-core, como
        una lectura mas de la tabla -- ``Resource.GOOGLE_TASKS``, con su
        propio proveedor y su propio codigo de error."""
        _fetch_resources(frozenset({Resource.GOOGLE_TASKS}))

    def _is_standby() -> bool:
        """Si el deck esta ahora mismo suspendido (pantalla apagada)."""
        with screen_lock:
            return screen.kind is screens.ScreenKind.STANDBY

    def _is_ticktick_active() -> bool:
        """Si la pantalla "TickTick" es la que esta activa ahora mismo.

        La usa el bucle principal para decidir si vale la pena disparar
        ``ticktick_refresh_cycle()`` en el ciclo periodico: solo mientras se
        esta viendo esa pantalla, nunca de fondo -- a diferencia de
        ``refresh_cycle()`` (habits-core), que siempre corre cada
        ``REFRESH_SECONDS`` sea cual sea la pantalla visible.
        """
        with screen_lock:
            return screen.kind is screens.ScreenKind.TICKTICK

    def _is_google_tasks_active() -> bool:
        """Si la pantalla "Google Tasks" es la que esta activa ahora mismo.
        Mismo papel exacto que ``_is_ticktick_active``, para el otro ciclo
        aparte de habits-core."""
        with screen_lock:
            return screen.kind is screens.ScreenKind.GOOGLE_TASKS

    def _enter_standby() -> None:
        """Apaga la retroiluminacion del deck y deja de refrescar.

        La dispara el temporizador de ``STANDBY_SECONDS`` sin pulsaciones y
        tambien el boton "Suspender" del submenu Sistema. La comprobacion de
        salida temprana no es solo defensiva: pulsar "Suspender" reprograma el
        temporizador de stand by (lo hace ``on_key_change`` en TODA pulsacion),
        asi que este volvera a disparar estando ya suspendido.

        Baja el brillo ANTES de pintar para que la transicion se lea como un
        fundido (el contenido anterior se apaga y luego aparece el icono) en vez
        de como un parpadeo de pantalla nueva a plena luz.

        Hay que pintar de verdad: ``BRIGHTNESS_STANDBY`` no es 0, asi que lo que
        hubiera antes se seguiria intuyendo. Lo que se ve sale de
        ``core.screens.STANDBY_LAYOUT``.
        """
        with screen_lock:
            if screen.kind is screens.ScreenKind.STANDBY:
                return
            screen.kind = screens.ScreenKind.STANDBY
            _safe_render(lambda: session.set_brightness(BRIGHTNESS_STANDBY))
            _paint_current_screen()
        print("Stand by: pantalla suspendida", flush=True)

    def _wake() -> None:
        """Sale del stand by: datos frescos primero, luz despues.

        **La unica navegacion que SI espera a la red**, a diferencia del resto
        (que pinta con cache y revalida por detras): el deck lleva suspendido
        de media hora para arriba, asi que lo cacheado no vale nada -- se
        invalida TODO y se leen, bloqueando, los datos de "Hoy". Como ese
        repintado ocurre con el brillo todavia a 0, el deck se enciende ya con
        el contenido correcto: sin destello de datos viejos ni doble
        repintado. A cambio tarda lo que tarde la red (1-2 s), que es
        exactamente lo que hacia antes.

        Lo que NO se pide aqui (plantillas, logs, cronometros...) queda
        invalidado, asi que lo pedira la primera pantalla que lo necesite.

        El centinela ``_claim`` sigue aqui (ya no en ``_enter_view``): dos
        pulsaciones seguidas a ciegas sobre un deck apagado no deben encadenar
        dos despertados.

        El ``finally`` no es decorativo: si el proveedor esta caido o falla el
        propio dispositivo, el deck TIENE que encenderse igual, o se quedaria
        negro para siempre y pareceria roto.
        """
        if not _claim(_NAV_SENTINEL):
            return  # ya hay un despertado en vuelo (doble toque sobre la pantalla apagada)
        try:
            with screen_lock:
                screen.kind, screen.view_id, screen.page = screens.ScreenKind.VIEW, screens.DEFAULT_VIEW_ID, 0
                screen.section_name = ""
                screen.project_name = ""
                needs = screens.needs_for(screen)
            # Invalidar ANTES de leer no es solo para tirar lo viejo: al subir
            # la generacion de todos los recursos, ninguna de estas lecturas
            # puede saltarse por single-flight (ver
            # core.cache.ResourceCache.begin_fetch), asi que _fetch_resources
            # SIEMPRE llega a repintar. Sin eso, un refresco en vuelo podria
            # dejar el deck encendido enseñando todavia la pantalla de stand by.
            cache.invalidate(ALL_RESOURCES)
            _fetch_resources(needs)  # bloqueante a proposito: datos frescos ANTES de encender
        finally:
            _release(_NAV_SENTINEL)
            _safe_render(lambda: session.set_brightness(BRIGHTNESS))
            print("Stand by: pantalla despertada", flush=True)

    def _enter_menu() -> None:
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.MENU, 0
            _paint_current_screen()
        _revalidate_screen()

    def _enter_system() -> None:
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.SYSTEM, 0
            _paint_current_screen()

    def _enter_sections_menu() -> None:
        """Abre el submenu "Secciones" (``core.screens.ScreenKind.SECTIONS_MENU``).

        Mismo patron que el resto de pantallas con datos: pinta al instante
        con los habitos ya cacheados (de ellos sale la lista, ver
        ``core.screens._section_menu_entries``) y solo los relee por detras si
        estan caducados."""
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.SECTIONS_MENU, 0
            _paint_current_screen()
        _revalidate_screen()

    def _enter_projects_menu() -> None:
        """Abre el submenu "Proyectos" (``core.screens.ScreenKind.PROJECTS_MENU``).

        Mirror exacto de ``_enter_sections_menu``, para tareas."""
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.PROJECTS_MENU, 0
            _paint_current_screen()
        _revalidate_screen()

    def _change_page(delta: int) -> None:
        with screen_lock:
            screen.page += delta
            _paint_current_screen()

    def _enter_numeric_entry(habit_id: str) -> None:
        """Abre el teclado numerico para ``habit_id``, sin tocar
        ``view_id``/``page``: es lo que permite que "Salir" vuelva
        exactamente a la vista de origen (ver ``core.screens.ScreenState``)."""
        with screen_lock:
            screen.kind = screens.ScreenKind.NUMERIC_ENTRY
            screen.entry_habit_id = habit_id
            screen.entry_value = ""
            _paint_current_screen()

    def _exit_numeric_entry() -> None:
        """Vuelve de la pantalla de teclado numerico a la vista de origen."""
        with screen_lock:
            screen.kind = screens.ScreenKind.VIEW
            _paint_current_screen()

    def _enter_item_options(item_kind: str, item_id: str) -> None:
        """Abre el menu de opciones de un habito/tarea, sin tocar
        ``view_id``/``page``: es lo que permite que "Volver" regrese
        exactamente a la vista de origen (ver ``core.screens.ScreenState``).
        La dispara solo el temporizador de mantener pulsado, nunca una
        pulsacion normal."""
        with screen_lock:
            screen.kind = screens.ScreenKind.ITEM_OPTIONS
            screen.entry_item_kind = item_kind
            screen.entry_item_id = item_id
            _paint_current_screen()

    def _exit_item_options() -> None:
        """Vuelve del menu de opciones de un habito/tarea a la vista de
        origen, sin ejecutar ninguna accion sobre el elemento que lo abrio."""
        with screen_lock:
            screen.kind = screens.ScreenKind.VIEW
            _paint_current_screen()

    def _enter_section_options(section_name: str, origin: screens.ScreenKind) -> None:
        """Abre la pantalla de opciones de ``section_name``
        (``ScreenKind.SECTION_OPTIONS``), sin tocar ``screen.page``: es lo que
        permite que "Volver" regrese exactamente a la misma pagina de la
        pantalla de origen en la que estabas.

        ``origin`` (``ScreenKind.SECTIONS_MENU`` o ``ScreenKind.MENU``, segun
        desde cual de las dos se mantuvo pulsado un boton de seccion, ver
        ``on_key_change``) se guarda en ``ScreenState.entry_section_origin``:
        es lo que permite que "Volver" (``_exit_section_options``) regrese a
        la pantalla correcta en vez de asumir siempre "Secciones" -- un boton
        de seccion ya fijado en el menu principal tambien abre esta pantalla
        al mantenerlo pulsado (no solo una entrada de "Secciones"). La
        dispara solo el temporizador de mantener pulsado, nunca una pulsacion
        normal."""
        with screen_lock:
            screen.kind = screens.ScreenKind.SECTION_OPTIONS
            screen.entry_section_name = section_name
            screen.entry_section_origin = origin
            _paint_current_screen()

    def _exit_section_options() -> None:
        """Vuelve de la pantalla de opciones de una seccion a la pantalla
        desde la que se abrio (``ScreenState.entry_section_origin``:
        "Secciones" o el menu principal, nunca ``ScreenKind.VIEW`` -- a
        diferencia de ``_exit_item_options``, aqui no hay ninguna vista de
        origen a la que volver)."""
        with screen_lock:
            screen.kind = screen.entry_section_origin
            _paint_current_screen()

    def _toggle_section_pin() -> None:
        """Alterna si la seccion abierta en ``ScreenKind.SECTION_OPTIONS``
        aparece como boton fijo en el menu principal (``core.pinned_sections``).

        A diferencia del resto de acciones que escriben algo (paso de habito,
        cierre de tarea, cambio de prioridad...), esto NO llama a ningun
        proveedor: es una escritura local a ``pinned_sections.json``, sin red,
        que no puede fallar con un ``ProviderError`` -- por eso no pasa por
        ``_claim``/``_release`` ni por el manejo de fallos de ``_run_action``,
        y por eso ``_dispatch_navigation`` (no ``_run_action``) es quien la
        ejecuta, junto al resto de navegacion pura.

        Se queda en ``SECTION_OPTIONS`` tras el toggle (no vuelve a
        "Secciones"): asi la etiqueta del boton cambia delante del usuario y
        se puede alternar varias veces sin tener que reabrir el menu, mismo
        criterio que "Ajustar el progreso" de un habito real
        (``_press_habit_options_delta``)."""
        nonlocal pinned_sections
        with screen_lock:
            section_name = screen.entry_section_name
        pinned_sections = pinned_sections_store.toggle(section_name, pinned_sections)
        with screen_lock:
            _paint_current_screen()

    def _enter_project_options(project_name: str, origin: screens.ScreenKind) -> None:
        """Abre la pantalla de opciones de ``project_name``
        (``ScreenKind.PROJECT_OPTIONS``). Mirror exacto de
        ``_enter_section_options``, para un proyecto -- ``origin`` es
        ``ScreenKind.PROJECTS_MENU`` o ``ScreenKind.MENU``, guardado en
        ``ScreenState.entry_project_origin``."""
        with screen_lock:
            screen.kind = screens.ScreenKind.PROJECT_OPTIONS
            screen.entry_project_name = project_name
            screen.entry_project_origin = origin
            _paint_current_screen()

    def _exit_project_options() -> None:
        """Vuelve de la pantalla de opciones de un proyecto a la pantalla
        desde la que se abrio (``ScreenState.entry_project_origin``). Mirror
        exacto de ``_exit_section_options``."""
        with screen_lock:
            screen.kind = screen.entry_project_origin
            _paint_current_screen()

    def _toggle_project_pin() -> None:
        """Alterna si el proyecto abierto en ``ScreenKind.PROJECT_OPTIONS``
        aparece como boton fijo en el menu principal (``core.pinned_projects``).
        Mirror exacto de ``_toggle_section_pin``: tampoco llama a ningun
        proveedor ni pasa por ``_claim``/``_release``."""
        nonlocal pinned_projects
        with screen_lock:
            project_name = screen.entry_project_name
        pinned_projects = pinned_projects_store.toggle(project_name, pinned_projects)
        with screen_lock:
            _paint_current_screen()

    def _numeric_edit(kind: str, digit: str) -> None:
        """Muta ``ScreenState.entry_value`` (teclear un digito, el punto
        decimal o borrar) y repinta. Sin llamada de red, asi que no pasa por
        ``_claim``/``_release``."""
        with screen_lock:
            if kind == "digit":
                if len(screen.entry_value) < _ENTRY_MAX_CHARS:
                    screen.entry_value += digit
            elif kind == "decimal":
                if "." not in screen.entry_value:
                    screen.entry_value = (screen.entry_value or "0") + "."
            elif kind == "backspace":
                screen.entry_value = screen.entry_value[:-1]
            _paint_current_screen()

    def _enter_view(view_id: str) -> None:
        """Cambia a ``view_id`` en pagina 0, la pinta YA con lo que haya en
        cache y, solo si esos datos estan caducados, los relee por detras
        (``_revalidate_screen``).

        Antes esto forzaba un refresco completo -- las ocho lecturas, en el
        hilo de callbacks -- en CADA entrada, aunque los datos tuvieran dos
        segundos: entrar en una vista no respondia hasta que terminaba la red
        y ademas releia cosas que esa vista ni pinta. Ahora la vista aparece
        al instante y solo se pide lo suyo, y solo si hace falta (ver
        ``core.screens.needs_for``/``max_age_for``).

        Ya no hace falta el centinela ``_claim``: el single-flight de
        ``core.cache.ResourceCache.begin_fetch`` es el que impide que un doble
        toque dispare dos veces la misma lectura.

        Limpia ``section_name``/``project_name``: sin esto, entrar en
        "Hoy"/"Hábitos"/"Tareas" desde el menu tras haber visitado una
        seccion/proyecto dejaria ese filtro puesto por error (ver
        ``core.screens.ScreenState.section_name``/``project_name``)."""
        with screen_lock:
            screen.kind, screen.view_id, screen.page = screens.ScreenKind.VIEW, view_id, 0
            screen.section_name = ""
            screen.project_name = ""
            _paint_current_screen()
        _revalidate_screen()

    def _enter_ticktick() -> None:
        """Entra en la pantalla principal de "TickTick" (``ScreenKind.TICKTICK``,
        ``ticktick_project_id`` vacio), la pinta con lo cacheado y revalida
        por detras si hace falta -- mismo patron que ``_enter_view``, pero lo
        unico que puede pedir aqui es ``Resource.TICKTICK`` (ver
        ``core.screens.needs_for``): esta pantalla es ajena a habits-core y
        nunca dispara sus lecturas, ni al reves.

        Limpia ``screen.ticktick_project_id``: sin esto, reabrir "TickTick"
        desde el menu tras haber entrado en un proyecto se quedaria filtrada
        por error (ver ``core.screens.ScreenState.ticktick_project_id``)."""
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.TICKTICK, 0
            screen.ticktick_project_id = ""
            _paint_current_screen()
        _revalidate_screen()

    def _enter_ticktick_project(project_id: str) -> None:
        """Entra en un proyecto de TickTick (filtra la pantalla "TickTick" a
        sus tareas), pulsando un boton de la pantalla principal.

        A diferencia de ``_enter_ticktick``/``_enter_section``/``_enter_project``,
        NO dispara ningun refresco: ``ticktick_tasks_ref`` ya trae TODAS las
        tareas (de cualquier proyecto, ver ``ticktick.client.TickTickApiProvider.
        get_tasks``) desde que se entro en la pantalla principal hace un
        instante, asi que filtrar por ``project_id`` es una operacion local
        (``core.screens.resolve_page``) -- gastar otra peticion a la API de
        TickTick solo por pulsar un boton no aportaria nada, y es justo el
        tipo de espera que se elimino al simplificar ``get_tasks()`` (ver
        CLAUDE.md, "Vista TickTick"). Sin ``_claim``/``_release`` por el mismo
        motivo: no hay red que pueda solaparse."""
        with screen_lock:
            screen.ticktick_project_id, screen.page = project_id, 0
            _paint_current_screen()

    def _exit_ticktick_project() -> None:
        """"Volver" desde un proyecto de TickTick a la pantalla principal
        (tecla 0, ver ``core.screens.resolve_press``): limpia
        ``screen.ticktick_project_id`` y repinta. Mismo patron que
        ``_enter_ticktick_project`` (sin refetch, sin ``_claim``/``_release``):
        los proyectos y tareas ya estan cargados, esto solo cambia el
        filtro."""
        with screen_lock:
            screen.ticktick_project_id, screen.page = "", 0
            _paint_current_screen()

    def _enter_google_tasks() -> None:
        """Entra en la pantalla principal de "Google Tasks"
        (``ScreenKind.GOOGLE_TASKS``, ``google_list_id`` vacio). Mismo patron
        exacto que ``_enter_ticktick``: pinta con lo cacheado y revalida por
        detras, y lo unico que puede pedir es ``Resource.GOOGLE_TASKS``.

        Limpia ``screen.google_list_id``: sin esto, reabrir "Google Tasks"
        desde el menu tras haber entrado en una lista se quedaria filtrada
        por error."""
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.GOOGLE_TASKS, 0
            screen.google_list_id = ""
            _paint_current_screen()
        _revalidate_screen()

    def _enter_google_list(list_id: str) -> None:
        """Entra en una lista de Google Tasks (filtra la pantalla "Google
        Tasks" a sus tareas). Mismo patron exacto que
        ``_enter_ticktick_project``: NO dispara ningun refresco --
        ``google_tasks_ref`` ya trae TODAS las tareas de TODAS las listas
        desde que se entro en la pantalla principal, asi que filtrar por
        ``list_id`` es una operacion local (``core.screens.resolve_page``)."""
        with screen_lock:
            screen.google_list_id, screen.page = list_id, 0
            _paint_current_screen()

    def _exit_google_list() -> None:
        """"Volver" desde una lista de Google Tasks a la pantalla principal
        (tecla 0). Mismo patron exacto que ``_exit_ticktick_project``: limpia
        ``screen.google_list_id`` y repinta, sin refetch."""
        with screen_lock:
            screen.google_list_id, screen.page = "", 0
            _paint_current_screen()

    def _enter_section(section_name: str) -> None:
        """Entra en la seccion ``section_name`` en pagina 0, pinta con la
        cache y revalida por detras -- mismo patron que ``_enter_view``, y
        como una seccion es "Habitos" filtrada, lo unico que puede pedir son
        los habitos (ver ``core.screens.needs_for``).

        ``screen.view_id`` no se toca (queda con lo que hubiera antes): no se
        consulta mientras ``section_name`` no este vacio (ver
        ``core.screens.resolve_page``). Limpia ``project_name`` (nunca los
        dos filtros a la vez)."""
        with screen_lock:
            screen.kind, screen.section_name, screen.page = screens.ScreenKind.VIEW, section_name, 0
            screen.project_name = ""
            _paint_current_screen()
        _revalidate_screen()

    def _enter_project(project_name: str) -> None:
        """Entra en el proyecto ``project_name`` en pagina 0. Mirror exacto de
        ``_enter_section``, para tareas (tecla 1 de "Tareas"/
        ``KEY_TASKS_PROJECTS_SHORTCUT``). Limpia ``section_name``."""
        with screen_lock:
            screen.kind, screen.project_name, screen.page = screens.ScreenKind.VIEW, project_name, 0
            screen.section_name = ""
            _paint_current_screen()
        _revalidate_screen()

    def _on_auto_return_timeout() -> None:
        """Vuelve a "Hoy" tras ``AUTO_RETURN_SECONDS`` sin pulsaciones fuera
        de esa vista. Repinta con los datos ya cacheados del ultimo ciclo,
        sin disparar un fetch nuevo: nunca hace I/O de red desde el hilo del
        temporizador, el ciclo periodico ya se encarga de mantenerlo fresco.
        """
        with screen_lock:
            if screen.kind is screens.ScreenKind.STANDBY:
                # Suspendido: no tocar. Normalmente este temporizador ya
                # disparo antes que el de stand by (5 min < 30 min), pero el
                # boton "Suspender" adelanta el stand by y lo deja armado.
                # Sin esta salida, a los 5 min sacaria de STANDBY sin encender
                # la pantalla: deck a oscuras con las teclas otra vez activas,
                # justo lo que "wake" existe para impedir.
                return
            at_home = (
                screen.kind is screens.ScreenKind.VIEW
                and screen.view_id == screens.DEFAULT_VIEW_ID
                and screen.page == 0
                and not screen.section_name
                and not screen.project_name
            )
            if at_home:
                return
            screen.kind, screen.view_id, screen.page = screens.ScreenKind.VIEW, screens.DEFAULT_VIEW_ID, 0
            screen.section_name = ""
            screen.project_name = ""
            _paint_current_screen()

    def _dispatch_navigation(action: screens.PressAction) -> None:
        """Ejecuta cualquier ``PressAction`` que no sea de habito, tarea o plantilla."""
        if action.kind == "open_menu":
            _enter_menu()
        elif action.kind == "open_system":
            _enter_system()
        elif action.kind == "open_sections":
            _enter_sections_menu()
        elif action.kind == "select_view":
            _enter_view(action.payload)
        elif action.kind == "enter_section":
            _enter_section(action.payload)
        elif action.kind == "open_projects":
            _enter_projects_menu()
        elif action.kind == "enter_project":
            _enter_project(action.payload)
        elif action.kind == "open_ticktick":
            _enter_ticktick()
        elif action.kind == "enter_ticktick_project":
            _enter_ticktick_project(action.payload)
        elif action.kind == "exit_ticktick_project":
            _exit_ticktick_project()
        elif action.kind == "open_google_tasks":
            _enter_google_tasks()
        elif action.kind == "enter_google_list":
            _enter_google_list(action.payload)
        elif action.kind == "exit_google_list":
            _exit_google_list()
        elif action.kind == "page_prev":
            _change_page(-1)
        elif action.kind == "page_next":
            _change_page(1)
        elif action.kind == "standby":
            _enter_standby()
        elif action.kind == "wake":
            _wake()
        elif action.kind == "shutdown":
            deck_keys.shutdown_pi()
        elif action.kind == "habit_enter_value":
            _enter_numeric_entry(action.payload)
        elif action.kind == "numeric_cancel":
            _exit_numeric_entry()
        elif action.kind == "numeric_digit":
            _numeric_edit("digit", action.payload)
        elif action.kind == "numeric_decimal":
            _numeric_edit("decimal", "")
        elif action.kind == "numeric_backspace":
            _numeric_edit("backspace", "")
        elif action.kind == "item_options_exit":
            _exit_item_options()
        elif action.kind == "section_options_exit":
            _exit_section_options()
        elif action.kind == "toggle_section_pin":
            _toggle_section_pin()
        elif action.kind == "project_options_exit":
            _exit_project_options()
        elif action.kind == "toggle_project_pin":
            _toggle_project_pin()

    # Dos plazos, el mismo disparador: cualquier pulsacion reprograma ambos.
    auto_return_timer = _IdleTimer(AUTO_RETURN_SECONDS, _on_auto_return_timeout)
    standby_timer = _IdleTimer(STANDBY_SECONDS, _enter_standby)

    def _reset_idle_timers() -> None:
        """Reprograma los dos temporizadores de inactividad. La llama
        ``make_key_callback`` en toda pulsacion, sea de la tecla que sea."""
        auto_return_timer.reset()
        standby_timer.reset()

    _reset_idle_timers()  # armados desde el arranque

    # Dos temporizadores periodicos mientras haya un cronometro corriendo,
    # para que el tiempo transcurrido se vea al dia sin esperar a
    # REFRESH_SECONDS (15 min) ni a que el usuario pulse algo. A diferencia
    # de auto_return_timer/standby_timer, ninguno de los dos se reprograma
    # con la actividad del usuario: son ciclos propios, independientes, que
    # se paran solos (_timer_tick_stop) al cerrar el daemon.
    _timer_tick_stop = threading.Event()

    def _timer_tick() -> None:
        """Cada ``TIMER_TICK_SECONDS`` (1s): repinta sin refetch. Usa
        running_timer_ref ya cacheado -- el calculo del tiempo transcurrido
        lo hace deck.renderer a partir de su started_at en cada repintado,
        nunca un contador que incrementa aqui."""
        if not _is_standby() and running_timer_ref["value"] is not None:
            _repaint_locked()
        if not _timer_tick_stop.is_set():
            threading.Timer(TIMER_TICK_SECONDS, _timer_tick).start()

    def _timer_sync() -> None:
        """Cada ``TIMER_SYNC_SECONDS`` (60s): relee SOLO lo de cronometros
        (``TIMER_RESOURCES``: cual corre y los dos acumulados) para corregir
        el tiempo que viene calculando ``_timer_tick`` en el cliente --
        deriva de reloj, o que otro cliente haya parado/arrancado el
        cronometro entre medias.

        Antes esto disparaba el ciclo completo, o sea las ocho lecturas cada
        minuto entero mientras un cronometro siguiera corriendo, para
        corregir un reloj. Ahora son tres, que es justo lo que puede haber
        cambiado."""
        if not _is_standby() and running_timer_ref["value"] is not None:
            _fetch_resources(TIMER_RESOURCES)
        if not _timer_tick_stop.is_set():
            threading.Timer(TIMER_SYNC_SECONDS, _timer_sync).start()

    threading.Timer(TIMER_TICK_SECONDS, _timer_tick).start()
    threading.Timer(TIMER_SYNC_SECONDS, _timer_sync).start()

    try:
        while True:
            try:
                # En stand by no se refresca: no tiene sentido pedir datos ni
                # repintar una pantalla apagada. El bucle sigue despertando
                # cada REFRESH_SECONDS sin hacer nada; el despertado fuerza su
                # propio ciclo completo (ver _wake).
                if not _is_standby():
                    refresh_cycle()
                    # TickTick/Google Tasks son ciclos aparte, y cada uno solo
                    # corre mientras su propia pantalla siga activa (ver
                    # _is_ticktick_active/_is_google_tasks_active): no tiene
                    # sentido pedirle datos a su API si nadie la esta mirando.
                    if _is_ticktick_active():
                        ticktick_refresh_cycle()
                    if _is_google_tasks_active():
                        google_tasks_refresh_cycle()
            except Exception as exc:
                # Cualquier fallo que no sea del proveedor de habitos/tareas
                # (esos ya se gestionan dentro de refresh_cycle) se trata
                # como error de dispositivo: nunca se muestra en tecla, solo
                # a fichero.
                health.log_device_error(str(exc))
                print(f"Error de dispositivo, intentando reconectar: {exc}", flush=True)
                session.reconnect()
                # reconnect() reabre con BRIGHTNESS: sin esto, un fallo de
                # dispositivo durante el stand by encenderia el deck sin que
                # nadie lo haya pulsado.
                if _is_standby():
                    _safe_render(lambda: session.set_brightness(BRIGHTNESS_STANDBY))
            time.sleep(REFRESH_SECONDS)
    finally:
        auto_return_timer.cancel()
        standby_timer.cancel()
        _timer_tick_stop.set()
        fetch_executor.shutdown(wait=False)
        session.close()


if __name__ == "__main__":
    main()
