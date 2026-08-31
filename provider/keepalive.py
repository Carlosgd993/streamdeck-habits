"""Reactivacion best-effort de un proyecto Supabase pausado por inactividad.

El plan gratuito pausa un proyecto tras ~1 semana sin trafico: su subdominio
de API deja de resolver por DNS, lo que ``provider.supabase`` traduce en un
``ProviderNetworkError`` (codigo ``NET`` en tecla) identico al de "la Pi no
tiene red" -- son indistinguibles desde el propio checkin. ``orchestrator``
usa este modulo para, ante un NET persistente, pedir la reactivacion a la
Management API de Supabase (``api.supabase.com``), que es una API y una
credencial totalmente distintas de las que usa ``provider.supabase`` contra
PostgREST -- de ahi que viva en su propio fichero en vez de en
``supabase.py``.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from config import ENV_FILE
from provider.supabase import ACTIVE_ENV_VAR, DEFAULT_ACTIVE_ENV, URL_ENV_VAR

TOKEN_ENV_VAR = "SUPABASE_ACCESS_TOKEN"  # Personal Access Token de Supabase, NO la clave publishable
_MANAGEMENT_BASE = "https://api.supabase.com/v1"


def _active_project_ref() -> str | None:
    """El ref del proyecto activo (subdominio de ``SUPABASE_URL``/``_TEST``),
    o ``None`` si no se puede determinar. Reutiliza el mismo ``SUPABASE_ENV``
    que ``provider.supabase`` para no requerir configuracion duplicada."""
    load_dotenv(ENV_FILE)
    active = os.environ.get(ACTIVE_ENV_VAR, DEFAULT_ACTIVE_ENV).strip().lower()
    suffix = "" if active == DEFAULT_ACTIVE_ENV else f"_{active.upper()}"
    url = os.environ.get(f"{URL_ENV_VAR}{suffix}")
    if not url:
        return None
    host = urlparse(url).hostname or ""
    ref = host.split(".")[0]
    return ref or None


def try_restore_active_project() -> bool:
    """Pide a la Management API que reactive el proyecto Supabase activo.

    Sin ``SUPABASE_ACCESS_TOKEN`` en el ``.env``, o sin poder determinar el
    ref del proyecto activo, no hace nada: es un intento best-effort que
    nunca debe tumbar el ciclo de refresco que lo dispara (ver
    ``orchestrator.refresh_cycle``), y un daemon sin ese token configurado
    debe seguir funcionando exactamente como antes de este modulo existir.

    Returns:
        ``True`` si la peticion de reactivacion se envio con exito (2xx);
        ``False`` en cualquier otro caso (sin token, sin ref, fallo de red o
        de la API). Un ``True`` no significa que el proyecto ya este listo:
        reactivar tarda uno o dos minutos, se confirma en un ciclo posterior.
    """
    load_dotenv(ENV_FILE)
    token = os.environ.get(TOKEN_ENV_VAR)
    ref = _active_project_ref()
    if not token or not ref:
        return False
    try:
        resp = requests.post(
            f"{_MANAGEMENT_BASE}/projects/{ref}/restore",
            headers={"Authorization": f"Bearer {token}"},
            json={},
            timeout=10,
        )
    except requests.RequestException:
        return False
    return resp.ok
