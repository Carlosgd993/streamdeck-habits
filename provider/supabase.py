"""Adaptador de Supabase: implementa el puerto ``HabitProvider`` contra la base
de datos Postgres del proyecto, expuesta via PostgREST (``<url>/rest/v1``).

Concentra TODO lo especifico de Supabase/PostgREST, aislado del resto del
proyecto:

- Carga de la URL y la clave publishable desde el ``.env``.
- Las llamadas HTTP (``requests``) contra ``rest/v1/habits`` y
  ``rest/v1/habit_checkins``.
- El mapeo de la fila cruda de la tabla ``habits`` al modelo de dominio
  agnostico (``provider.base.Habit`` y subtipos).
- La traduccion de cualquier fallo (``requests``, status, JSON) a la jerarquia
  de excepciones agnostica ``Provider*Error``.

El esquema de las tablas esta documentado en ``.claude/estructura-bd.md``; las columnas
relevantes para habitos son ``id``, ``name``, ``icon_res``, ``type``
(``Boolean``/``Real``), ``goal``, ``step``, ``unit``, ``sort_order``, ``status``
(0=activo, 1=archivado), y en ``habit_checkins``: ``habit_id``,
``checkin_date`` (``date``), ``value``, ``goal``, con indice unico
``(habit_id, checkin_date)`` para el upsert por dia.
"""

from __future__ import annotations

import os
from datetime import date
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
    Progress,
    RealHabit,
)

URL_ENV_VAR = "SUPABASE_URL"
KEY_ENV_VAR = "SUPABASE_PUBLISHABLE_KEY"
_ICON_TEXT_PREFIX = "txt_"  # prefijo de icon_res cuando el icono elegido es un emoji
_ACTIVE_STATUS = 0  # habits.status: 0=activo, 1=archivado


def _load_config() -> tuple[str | None, str | None]:
    """Carga el ``.env`` y devuelve ``(url, publishable_key)`` de Supabase."""
    load_dotenv(ENV_FILE)
    return os.environ.get(URL_ENV_VAR), os.environ.get(KEY_ENV_VAR)


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
    """Mapea una fila cruda de la tabla ``habits`` al modelo de dominio.

    Enruta ``type == "Real"`` a ``RealHabit`` (con ``goal``/``step``/``unit``) y
    todo lo demas -- incluido ``"Boolean"`` y tipos desconocidos -- a
    ``BooleanHabit``. El ``order`` se toma de ``sort_order`` (0 si falta).

    Args:
        data: Fila del habito devuelta por PostgREST.

    Returns:
        La instancia de ``Habit`` correspondiente al tipo del habito.
    """
    id = data["id"]
    name = data["name"]
    emoji = _extract_emoji_icon(str(data.get("icon_res") or ""))
    order = int(data.get("sort_order") or 0)

    if data.get("type") == "Real":
        return RealHabit(
            id=id,
            name=name,
            emoji=emoji,
            order=order,
            goal=float(data.get("goal", 1.0)),
            step=float(data.get("step", 1.0)),
            unit=str(data.get("unit") or ""),
        )
    return BooleanHabit(id=id, name=name, emoji=emoji, order=order)


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
            raise ProviderAuthError(f"Faltan {URL_ENV_VAR}/{KEY_ENV_VAR} en {ENV_FILE}")
        self._base = f"{url.rstrip('/')}/rest/v1"
        self._key = key

    def _headers(self, **extra: str) -> dict[str, str]:
        """Cabeceras base de PostgREST (``apikey`` + ``Authorization``)."""
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}", **extra}

    @staticmethod
    def _check_status(resp: requests.Response, what: str) -> None:
        """Traduce un status HTTP no exitoso a la excepcion agnostica adecuada."""
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"Clave invalida o sin permiso ({resp.status_code})")
        if resp.status_code not in (200, 201, 204):
            raise ProviderDataError(f"{what} -> status {resp.status_code}: {resp.text}")

    def get_habits(self) -> list[Habit]:
        """Devuelve los habitos activos del usuario, ya mapeados a dominio."""
        try:
            resp = requests.get(
                f"{self._base}/habits",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "id,name,icon_res,type,goal,step,unit,sort_order",
                    "status": f"eq.{_ACTIVE_STATUS}",
                    "order": "sort_order",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET habits")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET habits -> respuesta no es JSON valido") from exc
        return [build_habit(h) for h in raw]

    def get_progress(self, habit_ids: list[str], day: date) -> dict[str, Progress]:
        """Devuelve el progreso de cada habito en ``day``.

        Consulta ``habit_checkins`` filtrando por los ids dados y por la fecha
        exacta (``checkin_date`` es un ``date``).
        """
        if not habit_ids:
            return {}

        try:
            resp = requests.get(
                f"{self._base}/habit_checkins",
                headers=self._headers(Accept="application/json"),
                params={
                    "select": "habit_id,value,goal",
                    "habit_id": f"in.({','.join(habit_ids)})",
                    "checkin_date": f"eq.{day.isoformat()}",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET habit_checkins")
        try:
            rows = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET habit_checkins -> respuesta no es JSON valido") from exc

        return {
            row["habit_id"]: Progress(
                value=float(row.get("value", 0.0)),
                goal=float(row.get("goal", 1.0)),
            )
            for row in rows
        }

    def checkin(self, habit: Habit, day: date, value: float) -> None:
        """Registra el progreso total (``value``) de ``habit`` en ``day``.

        Hace un upsert por ``(habit_id, checkin_date)``: si ya existe la fila del
        dia se actualiza (``resolution=merge-duplicates`` sobre el indice unico),
        y si no se inserta. El checkin no es incremental: ``value`` es el total
        ya calculado por el llamador (via ``habit.next_value``).
        """
        payload = {
            "habit_id": habit.id,
            "checkin_date": day.isoformat(),
            "value": value,
            "goal": habit.goal,
        }
        try:
            resp = requests.post(
                f"{self._base}/habit_checkins",
                headers=self._headers(
                    **{"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
                ),
                params={"on_conflict": "habit_id,checkin_date"},
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST habit_checkins")
