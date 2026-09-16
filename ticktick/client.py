"""Adaptador de la API abierta de TickTick (``api.ticktick.com/open/v1``):
implementa ``TickTickProvider`` contra un ``access_token`` ya obtenido (esta
PoC no implementa el flujo OAuth2 con navegador -- ver ``ticktick-doc.txt``,
seccion "Authorization" -- el usuario lo genera una vez fuera de este
cliente y lo guarda en el ``.env``).

Concentra todo lo especifico de TickTick, igual que ``provider/supabase.py``
concentra todo lo de Supabase: no se importa nada de aqui fuera de
``ticktick/`` salvo ``core.emoji``/``provider.base.clip_title`` (utilidades
puras, reutilizables por cualquier adaptador) y ``config.ENV_FILE``.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from config import ENV_FILE
from core.emoji import extract_emoji
from provider.base import ProviderAuthError, ProviderDataError, ProviderNetworkError
from ticktick.base import TickTickProject, TickTickProvider, TickTickTask

TOKEN_ENV_VAR = "TICKTICK_ACCESS_TOKEN"
_BASE_URL = "https://api.ticktick.com/open/v1"


def _load_token() -> str | None:
    """Carga el ``.env`` (el mismo fichero que Supabase, ver ``config.ENV_FILE``)
    y devuelve el access token de TickTick, o ``None`` si falta."""
    load_dotenv(ENV_FILE)
    return os.environ.get(TOKEN_ENV_VAR)


def build_ticktick_task(data: dict[str, Any], project_id: str) -> TickTickTask:
    """Mapea una tarea cruda de TickTick al modelo de dominio.

    Comun a las dos fuentes que devuelven tareas con esta misma forma:
    ``ProjectData.tasks`` de ``GET /project/{id}/data`` (usa
    ``TickTickApiProvider.get_project_tasks``) y el array de
    ``POST /task/filter`` (usa ``TickTickApiProvider.get_tasks``) -- las dos
    ya vienen filtradas a pendientes por el servidor.

    Separa el emoji del titulo con ``core.emoji.extract_emoji`` -- mismo
    truco que ``provider.supabase.build_task`` para las tareas de
    habits-core: TickTick no expone un campo de icono propio, pero es
    habitual escribir el emoji dentro del titulo.

    Args:
        data: Una tarea cruda de cualquiera de las dos fuentes de arriba.
        project_id: Id del proyecto al que pertenece (redundante con
            ``data["projectId"]``, pero se pasa explicito porque el llamador
            ya lo conoce del bucle exterior, y en ``get_tasks`` es mas
            directo que volver a leerlo de ``data``).

    Returns:
        La ``TickTickTask`` correspondiente.
    """
    title = str(data.get("title") or "")
    emoji, title = extract_emoji(title)
    return TickTickTask(
        id=data["id"],
        project_id=str(data.get("projectId") or project_id),
        title=title,
        emoji=emoji,
        priority=int(data.get("priority") or 0),
        completed=int(data.get("status") or 0) != 0,
    )


def build_ticktick_project(data: dict[str, Any]) -> TickTickProject:
    """Mapea un proyecto crudo de ``GET /project`` al modelo de dominio."""
    return TickTickProject(
        id=data["id"],
        name=str(data.get("name") or ""),
        closed=bool(data.get("closed") or False),
        sort_order=int(data.get("sortOrder") or 0),
    )


class TickTickApiProvider(TickTickProvider):
    """Adaptador de ``TickTickProvider`` para la API abierta de TickTick."""

    def __init__(self) -> None:
        """Carga el access token del ``.env``.

        Raises:
            ProviderAuthError: Si falta ``TICKTICK_ACCESS_TOKEN``.
        """
        token = _load_token()
        if not token:
            raise ProviderAuthError(f"Falta {TOKEN_ENV_VAR} en {ENV_FILE}")
        self._token = token

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", **extra}

    @staticmethod
    def _check_status(resp: requests.Response, what: str) -> None:
        """Traduce un status HTTP no exitoso a la excepcion agnostica adecuada."""
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"Token invalido o caducado ({resp.status_code})")
        if resp.status_code not in (200, 201, 204):
            raise ProviderDataError(f"{what} -> status {resp.status_code}: {resp.text}")

    def get_projects(self) -> list[TickTickProject]:
        """Devuelve todos los proyectos de la cuenta via ``GET /project``
        (incluidos los ``closed``: los filtra ``core.screens.resolve_page``,
        no este adaptador).

        No la usa ``get_tasks`` (que trae todas las tareas de golpe sin
        pasar por proyectos, ver ahi mismo): la usa ``orchestrator.
        ticktick_refresh_cycle`` para los botones de proyecto de la pantalla
        "TickTick" (ver ``core.screens.ScreenKind.TICKTICK``).
        """
        try:
            resp = requests.get(f"{_BASE_URL}/project", headers=self._headers(Accept="application/json"), timeout=10)
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, "GET project")
        try:
            raw_projects: list[dict[str, Any]] = resp.json()
        except ValueError as exc:
            raise ProviderDataError("GET project -> respuesta no es JSON valido") from exc
        return [build_ticktick_project(p) for p in raw_projects]

    def get_project_tasks(self, project_id: str) -> list[TickTickTask]:
        """Devuelve las tareas pendientes de UN proyecto concreto, via
        ``GET /project/{id}/data`` -- su campo ``tasks`` ya son solo las
        pendientes, sin filtrado de status en el cliente.

        No la usa nada hoy: entrar en un proyecto desde la pantalla "TickTick"
        filtra localmente las tareas que ya trajo ``get_tasks()`` (todas de
        golpe, ver ahi mismo) en vez de gastar otra peticion. Se mantiene
        para el dia que haga falta pedir un proyecto suelto sin pasar por
        ``get_tasks()``. A diferencia de un filtro por ``projectIds`` en
        ``task/filter``, esta SI sirve para el Inbox: basta con pasarle su
        ``projectId`` (``"inbox" + id de usuario``, no documentado -- ver
        ``ticktick.http``).
        """
        try:
            resp = requests.get(
                f"{_BASE_URL}/project/{project_id}/data",
                headers=self._headers(Accept="application/json"),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, f"GET project/{project_id}/data")
        try:
            project_data = resp.json()
        except ValueError as exc:
            raise ProviderDataError(f"GET project/{project_id}/data -> respuesta no es JSON valido") from exc
        return [build_ticktick_task(t, project_id) for t in project_data.get("tasks", [])]

    def get_tasks(self) -> list[TickTickTask]:
        """Devuelve todas las tareas pendientes de golpe, de cualquier
        proyecto -- Inbox incluido -- en una unica peticion.

        ``POST /task/filter`` con ``{"status": [0]}`` y sin ``projectIds``
        trae de una sola vez las pendientes de todos los proyectos y del
        Inbox (que no tiene entrada en ``GET /project``, pero si un
        ``projectId`` interno con el que este filtro funciona igual que con
        cualquier otro -- ver ``ticktick.http``). No distingue proyectos
        ``closed``: una tarea pendiente en un proyecto archivado se sigue
        trayendo aqui igual, caso raro que se acepta a cambio de una sola
        peticion -- en la pantalla "TickTick" del deck, al no tener boton de
        proyecto (ver ``core.screens.resolve_page``), esa tarea se ve
        directamente en la pantalla principal, junto a las del Inbox.
        """
        try:
            resp = requests.post(
                f"{_BASE_URL}/task/filter",
                headers=self._headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
                json={"status": [0]},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, "POST task/filter")
        try:
            raw_tasks = resp.json()
        except ValueError as exc:
            raise ProviderDataError("POST task/filter -> respuesta no es JSON valido") from exc

        return [build_ticktick_task(t, str(t.get("projectId") or "")) for t in raw_tasks]

    def complete_task(self, task: TickTickTask) -> None:
        """Completa ``task`` via ``POST /project/{projectId}/task/{taskId}/complete``.

        Sin cuerpo que parsear (200/201 sin contenido), igual que
        ``SupabaseProvider.complete_task``.
        """
        try:
            resp = requests.post(
                f"{_BASE_URL}/project/{task.project_id}/task/{task.id}/complete",
                headers=self._headers(),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, "POST task/complete")

    def uncomplete_task(self, task: TickTickTask) -> None:
        """Reabre ``task`` via ``POST /task/{taskId}`` (Update Task) con ``status: 0``.

        No documentado en ``ticktick-doc.txt`` (que solo lista ``.../complete``
        para completar, sin endpoint inverso), pero validado a mano contra la
        API real antes de implementar esto: el body minimo (``id``,
        ``projectId``, ``status``) reabre la tarea sin tocar ningun otro
        campo -- no es un reemplazo completo.
        """
        try:
            resp = requests.post(
                f"{_BASE_URL}/task/{task.id}",
                headers=self._headers(**{"Content-Type": "application/json"}),
                json={"id": task.id, "projectId": task.project_id, "status": 0},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderNetworkError(str(exc)) from exc
        self._check_status(resp, "POST task (reabrir)")
