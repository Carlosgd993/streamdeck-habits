"""Adaptador de Supabase: implementa los puertos ``HabitProvider``,
``TaskProvider`` y ``TemplateProvider`` contra el contrato publico de la base de
datos, expuesto via PostgREST (``<url>/rest/v1``).

Concentra TODO lo especifico de Supabase/PostgREST, aislado del resto del
proyecto:

- Carga de la URL y la clave publishable desde el ``.env``.
- Las llamadas HTTP (``requests``) contra las vistas ``v_today_habits``,
  ``v_today_tasks`` y ``v_templates``, y las funciones ``rpc/habit_step``,
  ``rpc/habit_undo``, ``rpc/complete_task`` e ``rpc/instantiate_task`` del
  contrato.
- El mapeo de la fila cruda de la vista al modelo de dominio agnostico
  (``provider.base.Habit`` y subtipos, ``provider.base.Task``,
  ``provider.base.Template``).
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
    ProviderAuthError,
    ProviderDataError,
    ProviderNetworkError,
    RealHabit,
    Task,
    TaskProvider,
    Template,
    TemplateProvider,
)

URL_ENV_VAR = "SUPABASE_URL"
KEY_ENV_VAR = "SUPABASE_PUBLISHABLE_KEY"
ACTIVE_ENV_VAR = "SUPABASE_ENV"
DEFAULT_ACTIVE_ENV = "main"
_ICON_TEXT_PREFIX = "txt_"  # prefijo de icon_res cuando el icono elegido es un emoji
_TASKS_ORDER = "priority.desc,due_date.asc"  # v_today_tasks no ordena por si sola: lo mas urgente primero
_TEMPLATES_ORDER = "title"  # v_templates si ordena por dentro, pero el orden va explicito como en el resto


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


class SupabaseProvider(HabitProvider, TaskProvider, TemplateProvider):
    """Adaptador de los puertos ``HabitProvider``, ``TaskProvider`` y
    ``TemplateProvider`` para Supabase via PostgREST.

    Implementa los tres porque habitos, tareas y plantillas salen del mismo
    contrato y de la misma conexion; el resto del proyecto sigue dependiendo de
    los puertos por separado.
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
