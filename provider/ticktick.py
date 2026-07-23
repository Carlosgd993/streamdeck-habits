"""Adaptador de TickTick: implementa el puerto ``HabitProvider`` contra la API
abierta de TickTick (``api.ticktick.com/open/v1``).

Concentra TODO lo especifico de TickTick, aislado del resto del proyecto:

- Carga del token de acceso desde el ``.env``.
- Las llamadas HTTP (``requests``) contra ``open/v1/habit*``.
- El mapeo del JSON crudo de TickTick al modelo de dominio agnostico
  (``provider.base.Habit`` y subtipos).
- La traduccion de cualquier fallo (``requests``, status, JSON) a la jerarquia
  de excepciones agnostica ``Provider*Error``.

Para usar otra API, escribe otro modulo analogo a este que implemente
``HabitProvider`` y cambia la linea de construccion en ``orchestrator.py``.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
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

BASE_URL = "https://api.ticktick.com/open/v1"
HABIT_URL = f"{BASE_URL}/habit"
CHECKINS_URL = f"{BASE_URL}/habit/checkins"
CHECKIN_URL = BASE_URL + "/habit/{habit_id}/checkin"

TOKEN_ENV_VAR = "TICKTICK_ACCESS_TOKEN"
_ICON_TEXT_PREFIX = "txt_"  # prefijo de iconRes cuando el icono elegido es un emoji


def _load_token() -> str | None:
    """Carga el ``.env`` y devuelve el token de acceso de TickTick, o ``None``."""
    load_dotenv(ENV_FILE)
    return os.environ.get(TOKEN_ENV_VAR)


def _stamp(day: date) -> int:
    """Convierte una fecha al ``stamp`` entero ``YYYYMMDD`` que usa TickTick."""
    return int(day.strftime("%Y%m%d"))


def _extract_emoji_icon(data: dict[str, Any]) -> str:
    """Devuelve el emoji del icono del habito, o cadena vacia si no tiene.

    TickTick no guarda el emoji en ``name``: cuando el usuario elige un emoji
    como icono, ``iconRes`` vale ``"txt_<emoji>"`` (p.ej. ``"txt_🤸"``); los
    iconos predefinidos (no emoji) usan otros valores como
    ``"habit_daily_check_in"``.
    """
    icon_res = str(data.get("iconRes", ""))
    if not icon_res.startswith(_ICON_TEXT_PREFIX):
        return ""
    emoji, _ = extract_emoji(icon_res[len(_ICON_TEXT_PREFIX) :])
    return emoji


def build_habit(data: dict[str, Any]) -> Habit:
    """Mapea un habito crudo de TickTick al modelo de dominio agnostico.

    Enruta ``"Real"`` a ``RealHabit`` (con ``goal``/``step``/``unit``) y todo
    lo demas -- incluido ``"Boolean"`` y tipos desconocidos -- a
    ``BooleanHabit``. El ``order`` se toma de ``sortOrder`` (0 si falta).

    Args:
        data: Diccionario del habito devuelto por la API de TickTick.

    Returns:
        La instancia de ``Habit`` correspondiente al tipo del habito.
    """
    id = data["id"]
    name = data["name"]
    emoji = _extract_emoji_icon(data)
    order = int(data.get("sortOrder", 0))

    if data.get("type") == "Real":
        return RealHabit(
            id=id,
            name=name,
            emoji=emoji,
            order=order,
            goal=float(data.get("goal", 1.0)),
            step=float(data.get("step", 1.0)),
            unit=str(data.get("unit", "")),
        )
    return BooleanHabit(id=id, name=name, emoji=emoji, order=order)


class TickTickProvider(HabitProvider):
    """Adaptador del puerto ``HabitProvider`` para la API abierta de TickTick."""

    def __init__(self) -> None:
        """Carga el token del ``.env`` y prepara el cliente.

        Raises:
            ProviderAuthError: Si no hay ``TICKTICK_ACCESS_TOKEN`` definido.
        """
        token = _load_token()
        if not token:
            raise ProviderAuthError(f"Falta {TOKEN_ENV_VAR} en {ENV_FILE}")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _check_status(resp: requests.Response, what: str) -> None:
        """Traduce un status HTTP no exitoso a la excepcion agnostica adecuada."""
        if resp.status_code == 401:
            raise ProviderAuthError("Token invalido o expirado")
        if resp.status_code not in (200, 201):
            raise ProviderDataError(f"{what} -> status {resp.status_code}")

    def get_habits(self) -> list[Habit]:
        """Devuelve la lista de habitos del usuario, ya mapeados a dominio."""
        try:
            resp = requests.get(HABIT_URL, headers=self._headers(), timeout=10)
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET habit")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET habit -> respuesta no es JSON valido") from exc
        return [build_habit(h) for h in raw]

    def get_progress(self, habit_ids: list[str], day: date) -> dict[str, Progress]:
        """Devuelve el progreso de cada habito en ``day``.

        La API de TickTick trata ``to`` como limite EXCLUSIVO, asi que para
        incluir el propio ``day`` se pide hasta el dia siguiente.
        """
        if not habit_ids:
            return {}

        stamp = _stamp(day)
        next_day = _stamp(day + timedelta(days=1))

        try:
            resp = requests.get(
                CHECKINS_URL,
                headers=self._headers(),
                params={"habitIds": ",".join(habit_ids), "from": stamp, "to": next_day},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "GET habit/checkins")
        try:
            entries = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET habit/checkins -> respuesta no es JSON valido") from exc

        progress: dict[str, Progress] = {}
        for entry in entries:
            for checkin in entry.get("checkins") or []:
                if checkin.get("stamp") == stamp:
                    progress[entry["habitId"]] = Progress(
                        value=checkin.get("value", 0.0),
                        goal=checkin.get("goal", 1.0),
                    )
                    break
        return progress

    def checkin(self, habit: Habit, day: date, value: float) -> None:
        """Registra el progreso total (``value``) de ``habit`` en ``day``.

        El checkin de TickTick no es incremental: cada POST hace upsert del
        ``value`` total de ese ``stamp``, por eso ``value`` es el total ya
        calculado por el llamador (via ``habit.next_value``), no un delta.
        """
        payload = {"stamp": _stamp(day), "value": value, "goal": habit.goal}
        try:
            resp = requests.post(
                CHECKIN_URL.format(habit_id=habit.id),
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc

        self._check_status(resp, "POST habit/checkin")
