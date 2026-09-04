"""Adaptador de Supabase: implementa los puertos ``HabitProvider``,
``TaskProvider``, ``TemplateProvider`` y ``TimerProvider`` contra el contrato
publico de la base de datos, expuesto via PostgREST (``<url>/rest/v1``).

Concentra TODO lo especifico de Supabase/PostgREST, aislado del resto del
proyecto:

- Carga de la URL y la clave publishable desde el ``.env``.
- Las llamadas HTTP (``requests``) contra las vistas ``v_today_habits``,
  ``v_log_habits``, ``v_today_tasks``, ``v_templates``, ``v_timer_labels`` y
  ``v_running_timer``, y las funciones ``rpc/habit_step``, ``rpc/habit_undo``,
  ``rpc/complete_task``, ``rpc/skip_task``, ``rpc/set_task_priority``,
  ``rpc/instantiate_task`` y ``rpc/timer_toggle`` del contrato.
  ``v_log_habits`` reutiliza ``rpc/habit_step`` para el press -- no hay una
  RPC de registro aparte, el contrato es el mismo.
- El mapeo de la fila cruda de la vista al modelo de dominio agnostico
  (``provider.base.Habit`` y subtipos, ``provider.base.Task``,
  ``provider.base.Template``, ``provider.base.TimerLabel``,
  ``provider.base.RunningTimer``).
- La traduccion de cualquier fallo (``requests``, status, JSON) a la jerarquia
  de excepciones agnostica ``Provider*Error``.

El contrato (que vistas/funciones existen y que garantizan) esta documentado
en ``habits-core/docs/contrato.md``; este adaptador no conoce ni le hace falta
conocer ninguna tabla subyacente -- las tablas estan cerradas con RLS y solo
el contrato es accesible con la clave publishable.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from config import ENV_FILE
from core.emoji import extract_emoji
from provider.base import (
    BooleanHabit,
    Habit,
    HabitProvider,
    LogHabit,
    ProviderAuthError,
    ProviderDataError,
    ProviderNetworkError,
    RealHabit,
    RunningTimer,
    Task,
    TaskProvider,
    Template,
    TemplateProvider,
    TimerLabel,
    TimerProvider,
)

URL_ENV_VAR = "SUPABASE_URL"
KEY_ENV_VAR = "SUPABASE_PUBLISHABLE_KEY"
ACTIVE_ENV_VAR = "SUPABASE_ENV"
DEFAULT_ACTIVE_ENV = "main"
_ICON_TEXT_PREFIX = "txt_"  # prefijo de icon_res cuando el icono elegido es un emoji
_TASKS_ORDER = "priority.desc,due_date.asc"  # v_today_tasks no ordena por si sola: lo mas urgente primero
_TEMPLATES_ORDER = "title"  # v_templates si ordena por dentro, pero el orden va explicito como en el resto
_TIMER_LABELS_ORDER = "sort_order"  # v_timer_labels si ordena por dentro, pero explicito como el resto


def _load_config() -> tuple[str | None, str | None]:
    """Carga el ``.env`` y devuelve ``(url, publishable_key)`` del entorno activo.

    El ``.env`` trae las credenciales de los dos proyectos a la vez:
    ``SUPABASE_URL``/``SUPABASE_PUBLISHABLE_KEY`` para ``main`` (sin sufijo,
    es el caso normal) y ``SUPABASE_URL_TEST``/``SUPABASE_PUBLISHABLE_KEY_TEST``
    para ``test``. ``SUPABASE_ENV`` (``main`` o ``test``, por defecto ``main``)
    elige cual de las dos usar. Cambiar de proyecto es cambiar ``SUPABASE_ENV``
    en el ``.env`` y reiniciar el servicio -- nada mas.
    """
    load_dotenv(ENV_FILE)
    active = os.environ.get(ACTIVE_ENV_VAR, DEFAULT_ACTIVE_ENV).strip().lower()
    suffix = "" if active == DEFAULT_ACTIVE_ENV else f"_{active.upper()}"
    return os.environ.get(f"{URL_ENV_VAR}{suffix}"), os.environ.get(f"{KEY_ENV_VAR}{suffix}")


def _extract_emoji_icon(icon_res: str) -> str:
    """Devuelve el emoji del icono del habito, o cadena vacia si no tiene.

    Cuando el icono es un emoji se guarda como ``"txt_<emoji>"`` (p.ej.
    ``"txt_📖"``); los iconos predefinidos usan otros valores como
    ``"habit_water"`` y no aportan emoji.
    """
    if not icon_res.startswith(_ICON_TEXT_PREFIX):
        return ""
    emoji, _ = extract_emoji(icon_res[len(_ICON_TEXT_PREFIX) :])
    return emoji


def build_habit(data: dict[str, Any]) -> Habit:
    """Mapea una fila cruda de ``v_today_habits`` al modelo de dominio.

    Enruta ``type == "Real"`` a ``RealHabit`` (con ``goal``/``step``/``unit``) y
    todo lo demas -- incluido ``"Boolean"`` y tipos desconocidos -- a
    ``BooleanHabit``. El ``order`` se toma de ``sort_order`` (0 si falta).

    Args:
        data: Fila del habito devuelta por la vista, con el progreso de hoy
            ya incluido en ``current_value``.

    Returns:
        La instancia de ``Habit`` correspondiente al tipo del habito.
    """
    id = data["id"]
    name = data["name"]
    emoji = _extract_emoji_icon(str(data.get("icon_res") or ""))
    order = int(data.get("sort_order") or 0)
    current_value = float(data.get("current_value") or 0.0)

    if data.get("type") == "Real":
        return RealHabit(
            id=id,
            name=name,
            emoji=emoji,
            order=order,
            current_value=current_value,
            goal=float(data.get("goal", 1.0)),
            step=float(data.get("step", 1.0)),
            unit=str(data.get("unit") or ""),
            manual_entry=bool(data.get("manual_entry", False)),
        )
    return BooleanHabit(id=id, name=name, emoji=emoji, order=order, current_value=current_value)


def build_log_habit(data: dict[str, Any]) -> LogHabit:
    """Mapea una fila cruda de ``v_log_habits`` al modelo de dominio.

    Sin ramas de clase por ``type`` (a diferencia de ``build_habit``): un log
    no tiene objetivo que enseñar, asi que ``Boolean``/``Real`` nunca cambian
    la clase instanciada -- ambos son siempre ``LogHabit``. Pero ``type`` si
    decide ``LogHabit.cumulative`` (``"Real"`` acumula por dia, ``"Boolean"``
    es un registro unico): ver ``provider.base.LogHabit``. El color sale tal
    cual de la base (``"#RRGGBB"`` o ``None``); la conversion a RGB para
    Pillow es cosa de ``deck.renderer``, no de este adaptador.

    Args:
        data: Fila del habito de registro devuelta por la vista.

    Returns:
        El ``LogHabit`` correspondiente.
    """
    return LogHabit(
        id=data["id"],
        name=data["name"],
        emoji=_extract_emoji_icon(str(data.get("icon_res") or "")),
        order=int(data.get("sort_order") or 0),
        current_value=float(data.get("current_value") or 0.0),
        color=str(data.get("color") or ""),
        cumulative=data.get("type") == "Real",
        unit=str(data.get("unit") or ""),
    )


def build_task(data: dict[str, Any]) -> Task:
    """Mapea una fila cruda de ``v_today_tasks`` al modelo de dominio.

    La vista solo devuelve tareas pendientes, asi que no hay ningun estado que
    interpretar: basta con parsear los campos que se pintan en la tecla.

    Las tareas no tienen columna de icono, pero es habitual escribir el emoji
    dentro del propio titulo (``"Bano 🚽"``): se separa aqui para pintarlo como
    icono a color, igual que el ``icon_res`` de un habito.

    Args:
        data: Fila de la tarea devuelta por la vista.

    Returns:
        La instancia de ``Task`` correspondiente.
    """
    emoji, title = extract_emoji(str(data["title"]))
    return Task(
        id=data["id"],
        title=title,
        emoji=emoji,
        priority=int(data.get("priority") or 0),
        overdue=bool(data.get("overdue")),
        due_day=str(data.get("due_day") or ""),
        template_id=str(data.get("template_id") or ""),
    )


def build_template(data: dict[str, Any]) -> Template:
    """Mapea una fila cruda de ``v_templates`` al modelo de dominio.

    Mismo criterio de emoji que ``build_task``: las plantillas tampoco tienen
    columna de icono, asi que se saca del propio titulo.

    ``has_pending`` NO se rellena aqui: no sale de esta vista, lo calcula
    ``core.screens`` cruzando plantillas con tareas pendientes.

    Args:
        data: Fila de la plantilla devuelta por la vista.

    Returns:
        La instancia de ``Template`` correspondiente.
    """
    emoji, title = extract_emoji(str(data["title"]))
    return Template(
        id=data["id"],
        title=title,
        emoji=emoji,
        priority=int(data.get("priority") or 0),
    )


def build_timer_label(data: dict[str, Any]) -> TimerLabel:
    """Mapea una fila cruda de ``v_timer_labels`` al modelo de dominio.

    Mismo criterio de emoji que ``build_task``/``build_template``: sin
    columna de icono propia, se saca del nombre.

    ``running``/``started_at`` NO se rellenan aqui: no salen de esta vista,
    los calcula ``core.screens`` cruzando etiquetas con
    ``get_running_timer()`` -- igual que ``Template.has_pending``.

    Args:
        data: Fila de la etiqueta devuelta por la vista.

    Returns:
        La instancia de ``TimerLabel`` correspondiente.
    """
    emoji, name = extract_emoji(str(data["name"]))
    return TimerLabel(
        id=data["id"],
        name=name,
        emoji=emoji,
        order=int(data.get("sort_order") or 0),
    )


def build_running_timer(data: dict[str, Any]) -> RunningTimer:
    """Mapea una fila cruda de ``v_running_timer`` al modelo de dominio.

    ``title`` ya viene denormalizado desde la base (copiado de
    ``tasks.title``/``timer_labels.name`` en el momento de arrancar); mismo
    criterio de emoji que ``build_task``/``build_timer_label`` -- se separa
    aqui y se descarta (esta pantalla no tiene icono propio donde pintarlo):
    si el titulo lo llevaba, el texto que queda ya sale limpio.

    Args:
        data: Fila del cronometro en marcha devuelta por la vista.

    Returns:
        La instancia de ``RunningTimer`` correspondiente.
    """
    _, title = extract_emoji(str(data.get("title") or ""))
    return RunningTimer(
        id=data["id"],
        task_id=str(data.get("task_id") or ""),
        label_id=str(data.get("label_id") or ""),
        title=title,
        started_at=str(data.get("started_at") or ""),
    )


class SupabaseProvider(HabitProvider, TaskProvider, TemplateProvider, TimerProvider):
    """Adaptador de los puertos ``HabitProvider``, ``TaskProvider``,
    ``TemplateProvider`` y ``TimerProvider`` para Supabase via PostgREST.

    Implementa los cuatro porque habitos, tareas, plantillas y cronometros
    salen del mismo contrato y de la misma conexion; el resto del proyecto
    sigue dependiendo de los puertos por separado.
    """

    def __init__(self) -> None:
        """Carga la URL y la clave del ``.env`` y prepara el cliente.

        Raises:
            ProviderAuthError: Si falta ``SUPABASE_URL`` o
                ``SUPABASE_PUBLISHABLE_KEY``.
        """
        url, key = _load_config()
        if not url or not key:
            active = os.environ.get(ACTIVE_ENV_VAR, DEFAULT_ACTIVE_ENV).strip().lower()
            suffix = "" if active == DEFAULT_ACTIVE_ENV else f"_{active.upper()}"
            raise ProviderAuthError(
                f"Faltan {URL_ENV_VAR}{suffix}/{KEY_ENV_VAR}{suffix} en {ENV_FILE} "
                f"({ACTIVE_ENV_VAR}={active})"
            )
        self._base = f"{url.rstrip('/')}/rest/v1"
        self._key = key

    def _headers(self, **extra: str) -> dict[str, str]:
        """Cabeceras base de PostgREST (``apikey`` + ``Authorization``)."""
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}", **extra}

    @staticmethod
    def _check_status(resp: requests.Response, what: str) -> None:
        """Traduce un status HTTP no exitoso a la excepcion agnostica adecuada."""
        if resp.status_code in (401, 403):
            raise ProviderAuthError(
                f"Clave invalida o sin permiso ({resp.status_code}); "
                "revisa tambien si falta un GRANT del contrato para este objeto"
            )
        if resp.status_code not in (200, 201, 204):
            raise ProviderDataError(f"{what} -> status {resp.status_code}: {resp.text}")

    def get_habits(self) -> list[Habit]:
        """Devuelve los habitos de hoy, ya mapeados a dominio.

        Una sola peticion a ``v_today_habits``: la vista ya filtra los habitos
        activos y trae el progreso de hoy, asi que no hace falta filtrar por
        estado ni encadenar una segunda consulta de progreso.
        """
        try:
            resp = requests.get(
                f"{self._base}/v_today_habits",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "id,name,icon_res,type,goal,step,unit,manual_entry,sort_order,current_value",
                    "order": "sort_order",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_today_habits")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_today_habits -> respuesta no es JSON valido") from exc
        return [build_habit(h) for h in raw]

    def get_log_habits(self) -> list[Habit]:
        """Devuelve los habitos de solo registro, ya mapeados a dominio.

        Mismo patron que ``get_habits``: una sola peticion, esta vez a
        ``v_log_habits`` -- la vista ya filtra por ``purpose = 'log'`` y no
        expone ``goal``/``step``/``done`` porque no significan nada aqui.
        Tampoco ordena por si sola, igual que ``v_today_habits``. Pide
        ``type``/``unit`` ademas de lo minimo: ``type`` decide si el log
        acumula por dia o es un registro unico (``LogHabit.cumulative``, ver
        ``build_log_habit``).
        """
        try:
            resp = requests.get(
                f"{self._base}/v_log_habits",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "id,name,icon_res,color,type,unit,sort_order,current_value",
                    "order": "sort_order",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_log_habits")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_log_habits -> respuesta no es JSON valido") from exc
        return [build_log_habit(h) for h in raw]

    def step(self, habit: Habit) -> float:
        """Avanza un paso ``habit`` via ``rpc/habit_step`` y devuelve el nuevo total."""
        try:
            resp = requests.post(
                f"{self._base}/rpc/habit_step",
                headers=self._headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
                json={"p_habit_id": habit.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/habit_step")
        try:
            return float(resp.json())
        except ValueError as exc:
            raise ProviderDataError("POST rpc/habit_step -> respuesta no es un numero valido") from exc

    def undo(self, habit: Habit) -> float:
        """Retrocede ``habit`` via ``rpc/habit_undo`` y devuelve el nuevo total.

        Simetrica de ``step``: la base decide el valor nuevo (booleano -> ``0``,
        cuantificable -> ``value - step`` sin bajar de ``0``) y devuelve ``0`` si
        hoy no habia checkin, asi que repetirla es seguro.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/habit_undo",
                headers=self._headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
                json={"p_habit_id": habit.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/habit_undo")
        try:
            return float(resp.json())
        except ValueError as exc:
            raise ProviderDataError("POST rpc/habit_undo -> respuesta no es un numero valido") from exc

    def set_value(self, habit: Habit, value: float) -> float:
        """Fija el valor exacto de hoy de ``habit`` via ``rpc/habit_set``.

        Simetrica de ``step``/``undo`` en la forma, pero el valor lo decide el
        llamador: la base solo aplica ``greatest(p_value, 0)`` y hace upsert
        del checkin de hoy.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/habit_set",
                headers=self._headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
                json={"p_habit_id": habit.id, "p_value": value},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/habit_set")
        try:
            return float(resp.json())
        except ValueError as exc:
            raise ProviderDataError("POST rpc/habit_set -> respuesta no es un numero valido") from exc

    def get_tasks(self) -> list[Task]:
        """Devuelve las tareas pendientes de hoy, ya mapeadas a dominio.

        Una sola peticion a ``v_today_tasks``: la vista ya excluye las
        completadas y las omitidas, y arrastra las vencidas de dias anteriores
        (``overdue``). No ordena por si sola, asi que el orden va explicito en
        la peticion (``_TASKS_ORDER``).
        """
        try:
            resp = requests.get(
                f"{self._base}/v_today_tasks",
                headers=self._headers(Accept="application/json"),
                params={"select": "id,title,priority,overdue,due_day,template_id", "order": _TASKS_ORDER},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_today_tasks")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_today_tasks -> respuesta no es JSON valido") from exc
        return [build_task(t) for t in raw]

    def complete_task(self, task: Task) -> None:
        """Cierra ``task`` via ``rpc/complete_task``.

        La funcion devuelve ``void``, asi que PostgREST responde 204 sin cuerpo:
        no hay JSON que parsear y el propio status es toda la confirmacion. Es
        idempotente en la base, de modo que un reintento no duplica nada.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/complete_task",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"p_task_id": task.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/complete_task")

    def skip_task(self, task: Task) -> None:
        """Omite ``task`` via ``rpc/skip_task``.

        Misma forma que ``complete_task``: ``void`` en la base, 204 sin
        cuerpo, nada que parsear. Idempotente en la base.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/skip_task",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"p_task_id": task.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/skip_task")

    def set_priority(self, task: Task, priority: int) -> None:
        """Cambia la prioridad de ``task`` via ``rpc/set_task_priority``.

        Misma forma que ``complete_task``: ``void`` en la base, 204 sin
        cuerpo, nada que parsear. Solo toca ocurrencias pendientes -- sobre
        una ya completada/omitida o inexistente no hace nada, ni falla.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/set_task_priority",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"p_task_id": task.id, "p_priority": priority},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/set_task_priority")

    def get_templates(self) -> list[Template]:
        """Devuelve las plantillas de creacion rapida, ya mapeadas a dominio.

        Una sola peticion a ``v_templates``, filtrando por ``show_in_deck``: la
        vista devuelve TODAS las plantillas activas (tambien las que se
        materializan solas, que aqui no pintamos), y el filtro es del cliente a
        proposito -- asi otros clientes siguen viendo la lista completa con el
        mismo ``grant``.
        """
        try:
            resp = requests.get(
                f"{self._base}/v_templates",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "id,title,priority",
                    "show_in_deck": "eq.true",
                    "order": _TEMPLATES_ORDER,
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_templates")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_templates -> respuesta no es JSON valido") from exc
        return [build_template(t) for t in raw]

    def create_task(self, template: Template) -> str:
        """Crea una ocurrencia desde ``template`` via ``rpc/instantiate_task``.

        No manda ninguna fecha: sin ``p_due`` la base hace vencer la ocurrencia
        ahora, que es lo que la hace aparecer en ``v_today_tasks`` (regla del
        contrato: ningun cliente envia fechas).

        A diferencia de ``complete_task``, esta RPC devuelve el ``uuid`` de la
        ocurrencia nueva, o sea 200 con un string JSON -- hay cuerpo que parsear.
        Y **no es idempotente**: dos llamadas crean dos tareas.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/instantiate_task",
                headers=self._headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
                json={"p_template_id": template.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/instantiate_task")
        try:
            return str(resp.json())
        except ValueError as exc:
            raise ProviderDataError("POST rpc/instantiate_task -> respuesta no es JSON valido") from exc

    def get_timer_labels(self) -> list[TimerLabel]:
        """Devuelve las etiquetas rapidas de cronometro, ya mapeadas a dominio.

        Una sola peticion a ``v_timer_labels``, filtrando por ``show_in_deck``:
        mismo patron que ``get_templates`` -- la vista devuelve todas las
        etiquetas activas y el filtro es del cliente a proposito.
        """
        try:
            resp = requests.get(
                f"{self._base}/v_timer_labels",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "id,name,sort_order",
                    "show_in_deck": "eq.true",
                    "order": _TIMER_LABELS_ORDER,
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_timer_labels")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_timer_labels -> respuesta no es JSON valido") from exc
        return [build_timer_label(label) for label in raw]

    def get_running_timer(self) -> RunningTimer | None:
        """Devuelve el cronometro en marcha ahora mismo, o ``None``.

        ``v_running_timer`` trae como mucho una fila (garantizado por la
        base); ``None`` si la lista viene vacia.
        """
        try:
            resp = requests.get(
                f"{self._base}/v_running_timer",
                headers=self._headers(Accept="application/json"),
                params={"select": "id,task_id,label_id,title,started_at", "limit": "1"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_running_timer")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_running_timer -> respuesta no es JSON valido") from exc
        return build_running_timer(raw[0]) if raw else None

    def get_daily_totals(self) -> dict[str, int]:
        """Devuelve los segundos acumulados hoy, por tarea o etiqueta.

        Una sola peticion a ``v_timer_daily_totals``: cada fila trae
        ``task_id`` XOR ``label_id`` (garantizado por la base, igual que en
        ``v_running_timer``), asi que el id no nulo de cada fila basta como
        clave -- no hay que distinguir de cual de las dos tablas viene.
        """
        try:
            resp = requests.get(
                f"{self._base}/v_timer_daily_totals",
                headers=self._headers(Accept="application/json"),
                params={"select": "task_id,label_id,seconds_today"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_timer_daily_totals")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_timer_daily_totals -> respuesta no es JSON valido") from exc
        return {row["task_id"] or row["label_id"]: row["seconds_today"] for row in raw}

    def get_task_totals(self) -> dict[str, int]:
        """Devuelve los segundos acumulados de siempre, por tarea.

        Una sola peticion a ``v_task_timer_totals``: al reves que
        ``get_daily_totals``, aqui solo hay ``task_id`` (una etiqueta no
        tiene "acumulado de siempre" en el contrato, solo el de hoy).
        """
        try:
            resp = requests.get(
                f"{self._base}/v_task_timer_totals",
                headers=self._headers(Accept="application/json"),
                params={"select": "task_id,seconds_total"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET v_task_timer_totals")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET v_task_timer_totals -> respuesta no es JSON valido") from exc
        return {row["task_id"]: row["seconds_total"] for row in raw}

    def toggle_task_timer(self, task: Task) -> None:
        """Alterna el cronometro de ``task`` via ``rpc/timer_toggle``.

        Misma forma que ``complete_task``: ``void`` en la base, 204 sin
        cuerpo, nada que parsear. La base decide start-vs-stop mirando su
        propio estado, no lo que este metodo asuma.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/timer_toggle",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"p_task_id": task.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/timer_toggle (task)")

    def toggle_label_timer(self, label: TimerLabel) -> None:
        """Alterna el cronometro de ``label`` via ``rpc/timer_toggle``.

        Misma forma que ``toggle_task_timer``, con ``p_label_id`` en vez de
        ``p_task_id`` -- la RPC exige exactamente uno de los dos.
        """
        try:
            resp = requests.post(
                f"{self._base}/rpc/timer_toggle",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"p_label_id": label.id},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST rpc/timer_toggle (label)")
