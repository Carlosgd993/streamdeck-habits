"""Adaptador de la API oficial de Google Tasks (``tasks.googleapis.com/tasks/v1``):
implementa ``GoogleTasksProvider`` con OAuth2 de aplicacion de escritorio, cuyo
``refresh_token`` ya se obtuvo una vez fuera de este cliente (ver
``scripts/google_oauth_setup.py``) y vive en el ``.env``.

Concentra todo lo especifico de Google, igual que ``ticktick/client.py``
concentra todo lo de TickTick: no se importa nada de aqui fuera de
``google_tasks/`` salvo ``core.emoji``/``provider.base.clip_title``
(utilidades puras) y ``config.ENV_FILE``.

**Unica diferencia estructural con ``ticktick/client.py``**: el access token
de Google caduca en un plazo corto (tipicamente 1 hora, ver ``expires_in`` en
la respuesta de renovacion) en vez de tener vida larga como el de TickTick, asi
que este adaptador guarda un ``refresh_token`` de larga duracion y renueva el
access token solo, bajo demanda (``_ensure_access_token``), en vez de leer un
access token ya listo del ``.env``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any

import requests
from dotenv import load_dotenv

from config import ENV_FILE
from core.emoji import extract_emoji
from google_tasks.base import GoogleTask, GoogleTaskList, GoogleTasksProvider
from provider.base import ProviderAuthError, ProviderDataError, ProviderNetworkError

CLIENT_ID_ENV_VAR = "GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN_ENV_VAR = "GOOGLE_REFRESH_TOKEN"

_API_BASE = "https://tasks.googleapis.com/tasks/v1"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

_TOKEN_REFRESH_MARGIN_SECONDS = 60
"""Renovar el access token cuando le queden menos de esto de vida, no justo al
caducar: evita que una peticion en curso reciba un 401 a mitad de camino por
haber caducado el token un instante despues de comprobarlo."""

_MAX_TASK_PAGES_PER_LIST = 5
"""Tope de seguridad al paginar las tareas de una lista (``nextPageToken``,
``maxResults=100``): con 500 tareas pendientes en una sola lista algo raro
esta pasando, y truncar en silencio esconderia tareas en vez de fallar de
forma visible."""


def _load_credentials() -> tuple[str, str, str] | None:
    """Carga el ``.env`` (el mismo fichero que Supabase/TickTick, ver
    ``config.ENV_FILE``) y devuelve ``(client_id, client_secret,
    refresh_token)``, o ``None`` si falta alguna de las tres."""
    load_dotenv(ENV_FILE)
    client_id = os.environ.get(CLIENT_ID_ENV_VAR)
    client_secret = os.environ.get(CLIENT_SECRET_ENV_VAR)
    refresh_token = os.environ.get(REFRESH_TOKEN_ENV_VAR)
    if not client_id or not client_secret or not refresh_token:
        return None
    return client_id, client_secret, refresh_token


def build_google_task(data: dict[str, Any], list_id: str) -> GoogleTask:
    """Mapea una tarea cruda de ``GET /lists/{id}/tasks`` al modelo de dominio.

    Separa el emoji del titulo con ``core.emoji.extract_emoji`` -- mismo
    truco que ``ticktick.client.build_ticktick_task``/
    ``provider.supabase.build_task``: Google Tasks no expone un campo de
    icono propio, pero es habitual escribir el emoji dentro del titulo.

    Args:
        data: Una tarea cruda de ``GET /lists/{id}/tasks``.
        list_id: Id de la lista a la que pertenece -- el recurso ``Task`` de
            Google no trae este campo (a diferencia de ``projectId`` en
            TickTick), asi que lo pasa quien ya lo conoce del bucle exterior
            (ver ``GoogleTasksApiProvider.get_tasks``).

    Returns:
        La ``GoogleTask`` correspondiente.
    """
    title = str(data.get("title") or "")
    emoji, title = extract_emoji(title)
    return GoogleTask(
        id=data["id"],
        list_id=list_id,
        title=title,
        emoji=emoji,
        due=str(data.get("due") or ""),
        parent=str(data.get("parent") or ""),
        position=str(data.get("position") or ""),
        completed=data.get("status") == "completed",
    )


def build_google_task_list(data: dict[str, Any]) -> GoogleTaskList:
    """Mapea una lista cruda de ``GET /users/@me/lists`` al modelo de dominio."""
    return GoogleTaskList(id=data["id"], title=str(data.get("title") or ""))


class GoogleTasksApiProvider(GoogleTasksProvider):
    """Adaptador de ``GoogleTasksProvider`` para la API oficial de Google Tasks."""

    def __init__(self) -> None:
        """Carga las credenciales OAuth2 del ``.env``.

        Raises:
            ProviderAuthError: Si falta ``GOOGLE_CLIENT_ID``/
                ``GOOGLE_CLIENT_SECRET``/``GOOGLE_REFRESH_TOKEN``.
        """
        credentials = _load_credentials()
        if credentials is None:
            raise ProviderAuthError(
                f"Falta {CLIENT_ID_ENV_VAR}/{CLIENT_SECRET_ENV_VAR}/{REFRESH_TOKEN_ENV_VAR} en {ENV_FILE}"
            )
        self._client_id, self._client_secret, self._refresh_token = credentials
        self._access_token: str | None = None
        self._access_token_expiry = 0.0  # time.monotonic() en que caduca el access token en memoria

    def _ensure_access_token(self) -> str:
        """Devuelve un access token valido, renovandolo contra Google si el
        que hay en memoria no existe o esta a punto de caducar.

        A diferencia de TickTick (access token de larga vida, pegado a mano
        en el ``.env``), aqui el access token dura poco: se guarda solo en
        memoria de este proceso (nunca en el ``.env``, que solo lleva el
        ``refresh_token`` de larga duracion) y se renueva bajo demanda.
        """
        if self._access_token is not None and time.monotonic() < self._access_token_expiry:
            return self._access_token
        try:
            resp = requests.post(
                _TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        if resp.status_code != 200:
            # El caso mas comun es "invalid_grant": el refresh token caduco
            # (7 dias si la app sigue en estado "Testing" en Google Cloud
            # Console, ver CLAUDE.md) o fue revocado a mano. Se trata como
            # AUTH igual que un 401: hace falta regenerarlo con
            # scripts/google_oauth_setup.py.
            raise ProviderAuthError(f"No se pudo renovar el access token (status {resp.status_code}): {resp.text}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderDataError("POST token -> respuesta no es JSON valido") from exc
        self._access_token = data["access_token"]
        expires_in = float(data.get("expires_in", 3600))
        self._access_token_expiry = time.monotonic() + expires_in - _TOKEN_REFRESH_MARGIN_SECONDS
        return self._access_token

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_access_token()}", **extra}

    @staticmethod
    def _check_status(resp: requests.Response, what: str) -> None:
        """Traduce un status HTTP no exitoso a la excepcion agnostica adecuada.

        Un 429 (limite de cuota) cae en ``ProviderDataError``, NO en
        ``ProviderAuthError``: es un problema de trafico, no de credencial, y
        pintarlo como ``AUTH`` empujaria a regenerar el token sin motivo.
        """
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"Token invalido o revocado ({resp.status_code})")
        if resp.status_code not in (200, 201, 204):
            raise ProviderDataError(f"{what} -> status {resp.status_code}: {resp.text}")

    def get_task_lists(self) -> list[GoogleTaskList]:
        """Devuelve todas las listas de la cuenta via ``GET /users/@me/lists``,
        para los botones de la pantalla principal de "Google Tasks" (ver
        ``core.screens.ScreenKind.GOOGLE_TASKS``)."""
        try:
            resp = requests.get(
                f"{_API_BASE}/users/@me/lists",
                headers=self._headers(Accept="application/json"),
                params={"maxResults": 1000},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, "GET users/@me/lists")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET users/@me/lists -> respuesta no es JSON valido") from exc
        return [build_google_task_list(item) for item in data.get("items", [])]

    def _get_list_tasks(self, list_id: str) -> list[GoogleTask]:
        """Tareas pendientes de UNA lista, siguiendo ``nextPageToken`` hasta
        ``_MAX_TASK_PAGES_PER_LIST`` paginas. ``showCompleted=false`` filtra en
        el servidor -- no hace falta filtrar en el cliente."""
        tasks: list[GoogleTask] = []
        page_token: str | None = None
        for _ in range(_MAX_TASK_PAGES_PER_LIST):
            params: dict[str, Any] = {"showCompleted": "false", "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = requests.get(
                    f"{_API_BASE}/lists/{list_id}/tasks",
                    headers=self._headers(Accept="application/json"),
                    params=params,
                    timeout=10,
                )
            except requests.RequestException as exc:
                raise ProviderNetworkError(str(exc)) from exc
            self._check_status(resp, f"GET lists/{list_id}/tasks")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderDataError(f"GET lists/{list_id}/tasks -> respuesta no es JSON valido") from exc
            tasks.extend(build_google_task(item, list_id) for item in data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return tasks

    def get_tasks(self, list_ids: Sequence[str]) -> list[GoogleTask]:
        """Devuelve las tareas pendientes de todas las listas en ``list_ids``,
        una peticion por lista (no hay equivalente al ``/task/filter`` de
        TickTick, ver ``google_tasks.base.GoogleTasksProvider``)."""
        tasks: list[GoogleTask] = []
        for list_id in list_ids:
            tasks.extend(self._get_list_tasks(list_id))
        return tasks

    def _patch_status(self, task: GoogleTask, status: str) -> None:
        try:
            resp = requests.patch(
                f"{_API_BASE}/lists/{task.list_id}/tasks/{task.id}",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"status": status},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, f"PATCH lists/{task.list_id}/tasks/{task.id} (status={status})")

    def complete_task(self, task: GoogleTask) -> None:
        """Completa ``task`` via ``PATCH .../tasks/{id}`` con
        ``{"status": "completed"}``. 200 con el recurso actualizado en el
        cuerpo, que este adaptador no necesita parsear: el llamador ya muta
        ``task.completed`` de forma optimista."""
        self._patch_status(task, "completed")

    def uncomplete_task(self, task: GoogleTask) -> None:
        """Reabre ``task`` via ``PATCH .../tasks/{id}`` con
        ``{"status": "needsAction"}`` -- operacion documentada y simetrica de
        ``complete_task``, a diferencia del Update no documentado que hace
        falta en TickTick."""
        self._patch_status(task, "needsAction")
