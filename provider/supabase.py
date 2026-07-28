"""Adaptador de Supabase: implementa el puerto ``HabitProvider`` contra el
contrato publico de la base de datos, expuesto via PostgREST (``<url>/rest/v1``).

Concentra TODO lo especifico de Supabase/PostgREST, aislado del resto del
proyecto:

- Carga de la URL y la clave publishable desde el ``.env``.
- Las llamadas HTTP (``requests``) contra la vista ``v_today_habits`` y la
  funcion ``rpc/habit_step`` del contrato.
- El mapeo de la fila cruda de la vista al modelo de dominio agnostico
  (``provider.base.Habit`` y subtipos).
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
)

URL_ENV_VAR = "SUPABASE_URL"
KEY_ENV_VAR = "SUPABASE_PUBLISHABLE_KEY"
ACTIVE_ENV_VAR = "SUPABASE_ENV"
DEFAULT_ACTIVE_ENV = "main"
_ICON_TEXT_PREFIX = "txt_"  # prefijo de icon_res cuando el icono elegido es un emoji


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
        )
    return BooleanHabit(id=id, name=name, emoji=emoji, order=order, current_value=current_value)


class SupabaseProvider(HabitProvider):
    """Adaptador del puerto ``HabitProvider`` para Supabase via PostgREST."""

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
                    "select": "id,name,icon_res,type,goal,step,unit,sort_order,current_value",
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
