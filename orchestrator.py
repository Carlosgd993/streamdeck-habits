#!/opt/streamdeck-habits/venv/bin/python
"""Punto de entrada del daemon: bucle de refresco que sincroniza los habitos,
las tareas pendientes y las plantillas de creacion rapida del proveedor con las
teclas del Stream Deck, y gestiona la navegacion por menu, la paginacion y los
pasos/deshaceres/cierres/creaciones al pulsar.

El orquestador depende solo de los puertos abstractos de ``provider.base``
(interfaces ``HabitProvider``/``TaskProvider``/``TemplateProvider``, modelos
``Habit``/``Task``/``Template`` y excepciones ``Provider*``) y del registro de
pantallas de ``core.screens``; la unica linea acoplada a un backend concreto es
la construccion del proveedor (``SupabaseProvider()``). Sustituir de API =
escribir otro adaptador que implemente esos puertos y cambiar esa linea.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import core.health as health
import core.key_map as key_map
import core.screens as screens
import deck.keys as deck_keys
import deck.renderer as renderer
from config import AUTO_RETURN_SECONDS, REFRESH_SECONDS, STANDBY_SECONDS
from core.error_codes import CODES
from deck.session import BRIGHTNESS, BRIGHTNESS_STANDBY, DeckSession
from provider.base import (
    Habit,
    HabitProvider,
    ProviderError,
    Task,
    TaskProvider,
    Template,
    TemplateProvider,
)
from provider.supabase import SupabaseProvider

state_lock = threading.Lock()
pending_requests: set[str] = set()  # ids (habito, tarea, plantilla o el centinela de navegacion) en vuelo
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
    mapping: dict[str, int],
    habits_ref: dict[str, dict[str, Habit]],
    tasks_ref: dict[str, dict[str, Task]],
    templates_ref: dict[str, dict[str, Template]],
    screen: screens.ScreenState,
    screen_lock: threading.Lock,
    reset_idle_timers: Callable[[], None],
    dispatch_navigation: Callable[[screens.PressAction], None],
    repaint: Callable[[], None],
    refresh: Callable[[], None],
    exit_numeric_entry: Callable[[], None],
) -> Callable[[Any, int, bool], None]:
    """Crea el callback de pulsacion de tecla para el estado actual.

    El closure resultante reprograma los temporizadores de inactividad en
    cualquier pulsacion, resuelve que tecla es (habito, tarea o accion de
    navegacion) contra la pagina vigente y actua en consecuencia:

    - **Habito**: pide al proveedor que avance un paso y, si tiene exito,
      repinta la **pantalla entera** (``repaint``, no solo esta tecla): en
      "Hoy" un habito que queda ``is_done`` desaparece de la lista y lo que
      quede se recoloca sin dejar hueco (ver ``core.screens._today_items``),
      asi que hace falta recalcular toda la pagina, no solo esta tecla. En
      "Habitos" el efecto visible es el de siempre (blanco/gris segun
      objetivo), porque esa vista no oculta nada. Fallo → tecla en rojo con
      codigo, sin tocar el resto. Una tecla con el objetivo ya alcanzado hoy
      se sigue pudiendo pulsar: es la base quien decide el nuevo valor
      (``habit_step``), y un habito cuantificable sigue sumando sin tope.
    - **Deshacer un habito**: la misma tecla, cuando ``core.screens``
      resuelve la pulsacion como "habit_undo" (un booleano ya hecho, en una
      vista que lo permita — hoy solo "Habitos"). Pide ``undo`` al proveedor
      y, si tiene exito, dispara un **refresco completo** (``refresh``) en vez
      del repintado optimista: el valor que devuelve la base es el del dia y
      en un habito ``weekly_quota`` la vista pinta el contador de la semana,
      asi que el unico estado fiable es el que se relee. Fallo → tecla en rojo
      con codigo, igual que un paso.
    - **Tarea**: la pinta en verde de acuse de recibo, pide cerrarla y, solo
      cuando la base lo confirma, la saca de ``tasks_ref`` y repinta la
      pantalla entera (``repaint``) para que las tareas restantes se
      recoloquen sin hueco, por el mismo motivo que un habito.
    - **Plantilla** (vista "Crear"): mismo acuse verde, pide crear la
      ocurrencia y, al confirmar la base, **anade la tarea nueva a
      ``tasks_ref``** y repinta. La plantilla **no** sale de ``templates_ref``
      (a diferencia de una tarea al cerrarse): sigue en pantalla, ahora en gris
      porque ya tiene ocurrencia abierta, y su tecla deja de hacer nada --
      ``instantiate_task`` no es idempotente. Ese gris sale solo de la tarea
      insertada, ver ``core.screens._create_items``, y ``core.screens`` ya
      devuelve "noop" en ese caso, asi que aqui no hay nada que comprobar.
    - **Navegacion** (menu, submenu Sistema, cambiar de vista, paginar,
      suspender, despertar, apagar): se delega entera en
      ``dispatch_navigation``, definido en ``main()`` porque necesita mutar el
      estado de pantalla compartido.
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
        mapping: Mapeo habito -> tecla vigente para este ciclo.
        habits_ref: Wrapper de un solo campo ``{"value": {id: Habit}}`` para
            que el closure observe actualizaciones de ciclos posteriores.
        tasks_ref: Idem para las tareas pendientes.
        templates_ref: Idem para las plantillas de creacion rapida.
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
        refresh: Ciclo de refresco completo (refetch + repintado), el mismo
            que corre periodicamente. Lo usa el deshacer de un habito, que
            necesita releer el estado real en vez de fiarse del valor
            devuelto.
        exit_numeric_entry: Vuelve de la pantalla de teclado numerico a la
            vista de origen y repinta. Lo usa una confirmacion ("OK") con
            exito; en un fallo se queda en el teclado (ver mas abajo) para
            poder reintentar sin volver a teclear.

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

    Returns:
        El callback ``on_key_change(deck, key, pressed)`` para el Stream Deck.
    """

    def press_habit(deck: Any, key: int, habit_id: str, *, undo: bool = False) -> None:
        habit = habits_ref["value"].get(habit_id)
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
            if undo:
                refresh()  # relee el estado real; ver el docstring de make_key_callback
            else:
                _safe_render(repaint)
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
            print(f"Entrada manual OK: {habit.name} -> {new_value}", flush=True)

    def press_task(deck: Any, key: int, task_id: str) -> None:
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
            # Confirmado por la base: la tarea deja de existir para el deck. Se
            # quita de tasks_ref para que otra pulsacion no reintente cerrarla,
            # y se repinta la pantalla entera para que lo que quede se recoloque.
            tasks_ref["value"].pop(task_id, None)
            _safe_render(repaint)
            print(f"Tarea completada: {task.title}", flush=True)

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
            print(f"Tarea creada desde plantilla: {template.title} -> {new_task_id}", flush=True)

    def on_key_change(deck: Any, key: int, pressed: bool) -> None:
        if not pressed:
            return
        reset_idle_timers()  # cualquier pulsacion, en cualquier pantalla, reprograma auto-retorno y stand by

        with screen_lock:
            habits_list = list(habits_ref["value"].values())
            tasks_list = list(tasks_ref["value"].values())
            templates_list = list(templates_ref["value"].values())
            resolved = screens.resolve_page(screen, habits_list, tasks_list, templates_list, mapping)
            action = screens.resolve_press(screen, key, resolved)

        if action.kind in ("habit", "habit_undo", "task", "template", "numeric_confirm"):
            # Un habito reserva su id sea cual sea la operacion, asi que un
            # paso/deshacer/confirmacion de entrada manual del mismo habito
            # tampoco pueden solaparse ("numeric_confirm" lleva el habit_id
            # como payload, ver core.screens.resolve_press).
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
                else:
                    press_habit(deck, key, item_id, undo=action.kind == "habit_undo")
            finally:
                _release(item_id)
        elif action.kind != "noop":
            dispatch_navigation(action)

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

    session = DeckSession()
    session.open()

    mapping = key_map.load_map()
    habits_ref: dict[str, dict[str, Habit]] = {"value": {}}  # habit_id -> objeto Habit, actualizado cada ciclo
    tasks_ref: dict[str, dict[str, Task]] = {"value": {}}  # task_id -> objeto Task, actualizado cada ciclo
    templates_ref: dict[str, dict[str, Template]] = {"value": {}}  # template_id -> Template, idem

    screen = screens.ScreenState()  # arranca en "Hoy", pagina 0
    screen_lock = threading.Lock()  # serializa screen/mapping entre el ciclo y los callbacks
    last_habits_code: str | None = None  # ultimo codigo de error de habitos, para pintarlo tras navegar sin refetch
    last_tasks_code: str | None = None  # idem para tareas
    last_templates_code: str | None = None  # idem para plantillas

    def _paint_current_screen() -> None:
        """Resuelve la pantalla activa contra los datos vigentes, la pinta y
        re-registra el callback de tecla.

        PRECONDICION: se llama siempre con ``screen_lock`` ya adquirido por
        el llamador (nunca lo adquiere el mismo).
        """
        deck = session.deck
        resolved = screens.resolve_page(
            screen,
            list(habits_ref["value"].values()),
            list(tasks_ref["value"].values()),
            list(templates_ref["value"].values()),
            mapping,
        )
        _safe_render(lambda: renderer.render_page(deck, resolved))

        # Los codigos de error se pintan DESPUES del repintado general, para
        # que tapen los datos viejos de la parte que fallo, y solo si la
        # pantalla visible los usa (un fallo de tareas no debe teñir "Sistema").
        # Los ids de vista van literales a proposito: es el unico sitio fuera de
        # core/screens.py que los conoce, y una vista nueva tiene que decidir
        # explicitamente que codigos le afectan.
        is_view = screen.kind is screens.ScreenKind.VIEW
        if last_habits_code is not None and is_view and screen.view_id in ("today", "habits"):
            code = last_habits_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_habit.keys(), code))
        if last_tasks_code is not None and is_view and screen.view_id in ("today", "tasks"):
            code = last_tasks_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_task.keys(), code))
        if last_templates_code is not None and is_view and screen.view_id == "create":
            code = last_templates_code
            _safe_render(lambda: renderer.render_error_all(deck, resolved.key_template.keys(), code))

        deck.set_key_callback(
            make_key_callback(
                deck,
                habit_provider,
                task_provider,
                template_provider,
                mapping,
                habits_ref,
                tasks_ref,
                templates_ref,
                screen,
                screen_lock,
                _reset_idle_timers,
                _dispatch_navigation,
                _repaint_locked,
                refresh_cycle,
                _exit_numeric_entry,
            )
        )

    def _repaint_locked() -> None:
        """Repinta la pantalla activa adquiriendo ``screen_lock`` (a
        diferencia de ``_paint_current_screen``, que asume el lock ya
        adquirido). La usa ``make_key_callback`` tras un paso de habito o un
        cierre de tarea con exito, fuera de cualquier ``with screen_lock``
        en curso, para reflejar de inmediato un cambio que puede desplazar
        otros items (p.ej. recolocar "Hoy" sin dejar hueco)."""
        with screen_lock:
            _paint_current_screen()

    def refresh_cycle() -> None:
        """Refetch + repintado de la pantalla activa.

        La llama el bucle principal cada ``REFRESH_SECONDS``, pero tambien el
        hilo de callbacks del deck: al entrar en una vista desde el menu
        (``_enter_view``) y al deshacer un habito. Adquiere ``screen_lock``
        ella misma, asi que solo puede llamarse SIN el lock ya adquirido.
        """
        nonlocal mapping, last_habits_code, last_tasks_code, last_templates_code
        with screen_lock:
            # Las tres lecturas se hacen por separado y fallan por separado: un
            # fallo leyendo tareas deja su codigo aparte y no toca los habitos
            # ni las plantillas, y asi con cualquiera. La lectura que falla no
            # toca su mapeo ni sus datos (se conservan los del ciclo anterior).
            try:
                habits = habit_provider.get_habits()
            except ProviderError as exc:
                _, last_habits_code = health.classify(exc)
                print(f"[{last_habits_code}] {CODES[last_habits_code]} (habitos): {exc}", flush=True)
            else:
                last_habits_code = None
                mapping = key_map.update_mapping(habits, mapping)
                habits_ref["value"] = {h.id: h for h in habits}

            try:
                tasks = task_provider.get_tasks()
            except ProviderError as exc:
                _, last_tasks_code = health.classify(exc)
                print(f"[{last_tasks_code}] {CODES[last_tasks_code]} (tareas): {exc}", flush=True)
            else:
                last_tasks_code = None
                tasks_ref["value"] = {t.id: t for t in tasks}
                if tasks:
                    print(f"{len(tasks)} tarea(s) pendientes", flush=True)

            try:
                templates = template_provider.get_templates()
            except ProviderError as exc:
                _, last_templates_code = health.classify(exc)
                print(f"[{last_templates_code}] {CODES[last_templates_code]} (plantillas): {exc}", flush=True)
            else:
                last_templates_code = None
                templates_ref["value"] = {t.id: t for t in templates}

            _paint_current_screen()

    def _is_standby() -> bool:
        """Si el deck esta ahora mismo suspendido (pantalla apagada)."""
        with screen_lock:
            return screen.kind is screens.ScreenKind.STANDBY

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

        ``_enter_view`` ya hace todo lo necesario (reserva el centinela de
        navegacion para que un doble toque no dispare dos refrescos, deja la
        pantalla en "Hoy" -- lo que de paso saca de ``STANDBY`` -- y fuerza un
        ``refresh_cycle`` completo), y como el repintado ocurre con el brillo
        todavia a 0, el deck se enciende ya con el contenido correcto: sin
        destello de datos viejos ni doble repintado.

        El ``finally`` no es decorativo: si el proveedor esta caido o falla el
        propio dispositivo, el deck TIENE que encenderse igual, o se quedaria
        negro para siempre y pareceria roto.
        """
        try:
            _enter_view(screens.DEFAULT_VIEW_ID)
        finally:
            _safe_render(lambda: session.set_brightness(BRIGHTNESS))
            print("Stand by: pantalla despertada", flush=True)

    def _enter_menu() -> None:
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.MENU, 0
            _paint_current_screen()

    def _enter_system() -> None:
        with screen_lock:
            screen.kind, screen.page = screens.ScreenKind.SYSTEM, 0
            _paint_current_screen()

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
        """Cambia a ``view_id`` en pagina 0 y fuerza un refresco completo
        (refetch + repintado): entrar en una vista desde el menu siempre
        pide datos frescos antes de pintarla."""
        if not _claim(_NAV_SENTINEL):
            return  # ya hay una entrada a vista en vuelo (doble toque en el menu)
        try:
            with screen_lock:
                screen.kind, screen.view_id, screen.page = screens.ScreenKind.VIEW, view_id, 0
            refresh_cycle()
        finally:
            _release(_NAV_SENTINEL)

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
            )
            if at_home:
                return
            screen.kind, screen.view_id, screen.page = screens.ScreenKind.VIEW, screens.DEFAULT_VIEW_ID, 0
            _paint_current_screen()

    def _dispatch_navigation(action: screens.PressAction) -> None:
        """Ejecuta cualquier ``PressAction`` que no sea de habito, tarea o plantilla."""
        if action.kind == "open_menu":
            _enter_menu()
        elif action.kind == "open_system":
            _enter_system()
        elif action.kind == "select_view":
            _enter_view(action.payload)
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

    # Dos plazos, el mismo disparador: cualquier pulsacion reprograma ambos.
    auto_return_timer = _IdleTimer(AUTO_RETURN_SECONDS, _on_auto_return_timeout)
    standby_timer = _IdleTimer(STANDBY_SECONDS, _enter_standby)

    def _reset_idle_timers() -> None:
        """Reprograma los dos temporizadores de inactividad. La llama
        ``make_key_callback`` en toda pulsacion, sea de la tecla que sea."""
        auto_return_timer.reset()
        standby_timer.reset()

    _reset_idle_timers()  # armados desde el arranque

    try:
        while True:
            try:
                # En stand by no se refresca: no tiene sentido pedir datos ni
                # repintar una pantalla apagada. El bucle sigue despertando
                # cada REFRESH_SECONDS sin hacer nada; el despertado fuerza su
                # propio ciclo completo (ver _wake).
                if not _is_standby():
                    refresh_cycle()
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
        session.close()


if __name__ == "__main__":
    main()
