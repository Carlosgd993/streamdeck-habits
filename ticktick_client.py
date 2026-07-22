"""Cliente HTTP de la API abierta de TickTick para habitos y checkins.

Envuelve ``requests`` y traduce cualquier fallo a una jerarquia de excepciones
propia (``TickTickError`` y subclases), en vez de dejar pasar excepciones de
``requests`` o devolver ``None``/``False``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import requests

BASE_URL = "https://api.ticktick.com/open/v1"
HABIT_URL = f"{BASE_URL}/habit"
CHECKINS_URL = f"{BASE_URL}/habit/checkins"
CHECKIN_URL = BASE_URL + "/habit/{habit_id}/checkin"


class TickTickError(Exception):
    """Base para cualquier fallo al hablar con la API de TickTick."""


class TickTickAuthError(TickTickError):
    """Token invalido o caducado (401)."""


class TickTickNetworkError(TickTickError):
    """Fallo de conexion (timeout, DNS, etc.) hacia TickTick."""


class TickTickAPIError(TickTickError):
    """La API respondio algo distinto de lo esperado."""


class TickTickClient:
    """Cliente de la API abierta de TickTick (``api.ticktick.com/open/v1``)."""

    def __init__(self, token: str) -> None:
        """Inicializa el cliente.

        Args:
            token: Token de acceso Bearer de TickTick.
        """
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def get_habits(self) -> list[dict[str, Any]]:
        """Devuelve la lista de habitos del usuario.

        Returns:
            Los habitos tal cual los serializa la API de TickTick.

        Raises:
            TickTickAuthError: Si el token es invalido o ha caducado (401).
            TickTickNetworkError: Si falla la conexion con TickTick.
            TickTickAPIError: Si la respuesta no es 200 o no es JSON valido.
        """
        try:
            resp = requests.get(HABIT_URL, headers=self._headers(), timeout=10)
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code != 200:
            raise TickTickAPIError(f"GET habit -> status {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TickTickAPIError("GET habit -> respuesta no es JSON valido") from exc

    def get_checkins_for(self, habit_ids: list[str], stamp: int) -> set[str]:
        """Devuelve el conjunto de habitos con un checkin completado en un dia.

        Un checkin cuenta como completado cuando ``value >= goal``. La API de
        TickTick trata ``to`` como limite EXCLUSIVO, asi que para incluir el
        propio dia de ``stamp`` se pide hasta el dia siguiente.

        Args:
            habit_ids: Ids de los habitos a consultar. Si esta vacio no se
                hace ninguna llamada de red.
            stamp: Dia a consultar en formato ``YYYYMMDD``.

        Returns:
            El conjunto de ids con checkin completado ese dia.

        Raises:
            TickTickAuthError: Si el token es invalido o ha caducado (401).
            TickTickNetworkError: Si falla la conexion con TickTick.
            TickTickAPIError: Si la respuesta no es 200 o no es JSON valido.
        """
        if not habit_ids:
            return set()

        next_day = int((datetime.strptime(str(stamp), "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d"))

        try:
            resp = requests.get(
                CHECKINS_URL,
                headers=self._headers(),
                params={"habitIds": ",".join(habit_ids), "from": stamp, "to": next_day},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code != 200:
            raise TickTickAPIError(f"GET habit/checkins -> status {resp.status_code}")

        try:
            entries = resp.json()
        except ValueError as exc:
            raise TickTickAPIError("GET habit/checkins -> respuesta no es JSON valido") from exc

        done = set()
        for entry in entries:
            for checkin in entry.get("checkins") or []:
                if checkin.get("stamp") == stamp and checkin.get("value", 0) >= checkin.get("goal", 1.0):
                    done.add(entry["habitId"])
                    break
        return done

    def create_checkin(self, habit_id: str, payload: dict[str, Any]) -> bool:
        """Registra un checkin para un habito.

        Args:
            habit_id: Id del habito sobre el que se hace checkin.
            payload: Cuerpo JSON del checkin (``stamp``, ``value``, ``goal``).

        Returns:
            ``True`` si el checkin se creo correctamente (200 o 201).

        Raises:
            TickTickAuthError: Si el token es invalido o ha caducado (401).
            TickTickNetworkError: Si falla la conexion con TickTick.
            TickTickAPIError: Si la respuesta no es 200/201.
        """
        try:
            resp = requests.post(
                CHECKIN_URL.format(habit_id=habit_id),
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code not in (200, 201):
            raise TickTickAPIError(f"POST habit/checkin -> status {resp.status_code}")
        return True
