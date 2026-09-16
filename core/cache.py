"""Caducidad de las lecturas del daemon: que datos hay que volver a pedir y
cuando, sin perder sincronia con la base.

Los datos en si siguen viviendo donde vivian (``orchestrator.main`` los guarda
en los wrappers ``*_ref``); lo que falta y aporta este modulo es **cuando se
leyo cada cosa por ultima vez**, para poder navegar sin repetir una peticion
que se hizo hace dos segundos. Tres piezas, todas puras y sin red:

- **``Resource``** -- el catalogo de lecturas independientes del daemon (una
  por peticion: habitos, tareas, plantillas...). Cada pantalla declara cuales
  necesita (``core.screens.needs_for``), asi que entrar en "Habitos" ya no
  pide tareas ni cronometros.
- **TTL** (``ResourceCache.stale``) -- un recurso leido hace menos de
  ``config.CACHE_TTL_SECONDS`` se da por bueno y no se vuelve a pedir. Es lo
  que hace gratis el ir y volver entre pantallas.
- **Generacion + single-flight** (``begin_fetch``/``end_fetch``/
  ``invalidate``) -- lo que impide que una lectura vieja pise una escritura
  reciente, y que dos navegaciones seguidas disparen la misma peticion dos
  veces. Ver ``end_fetch``.

Nada de esto decide logica de negocio: una escritura sigue yendo directa a su
RPC y es la base quien decide el resultado (ver CLAUDE.md). Esto solo evita
releer lo que ya se sabe.
"""

from __future__ import annotations

import threading
import time
from enum import StrEnum


class Resource(StrEnum):
    """Una lectura independiente del daemon: cada valor es una peticion.

    Los ocho primeros son las lecturas del contrato de ``habits-core`` (ver
    ``provider.supabase``); ``TICKTICK`` es la PoC aparte (ver
    ``ticktick.client``), que no comparte proveedor ni codigo de error con
    las anteriores pero si el mismo mecanismo de caducidad.
    """

    HABITS = "habits"
    LOG_HABITS = "log_habits"
    TASKS = "tasks"
    TEMPLATES = "templates"
    TIMER_LABELS = "timer_labels"
    RUNNING_TIMER = "running_timer"
    DAILY_TOTALS = "daily_totals"
    TASK_TOTALS = "task_totals"
    TICKTICK = "ticktick"


SUPABASE_RESOURCES = frozenset(
    {
        Resource.HABITS,
        Resource.LOG_HABITS,
        Resource.TASKS,
        Resource.TEMPLATES,
        Resource.TIMER_LABELS,
        Resource.RUNNING_TIMER,
        Resource.DAILY_TOTALS,
        Resource.TASK_TOTALS,
    }
)
"""Las lecturas de ``habits-core``. Aparte de ``TICKTICK`` porque solo estas
cuentan para decidir si hay que reactivar un proyecto Supabase pausado
(``orchestrator._maybe_restore_project``): un NET de la API de TickTick no
dice nada de Supabase."""

ALL_RESOURCES = frozenset(Resource)

HABIT_RESOURCES = frozenset({Resource.HABITS, Resource.LOG_HABITS})
"""Lo que invalida cualquier escritura sobre un habito. Van juntos a
proposito: un habito pulsado puede venir de cualquiera de las dos lecturas
(ver ``orchestrator.press_habit``) y no merece la pena distinguirlo."""

TIMER_RESOURCES = frozenset({Resource.RUNNING_TIMER, Resource.DAILY_TOTALS, Resource.TASK_TOTALS})
"""Lo que cambia al arrancar/parar un cronometro: cual corre y los dos
acumulados (hoy por tarea/etiqueta, y de siempre por tarea)."""

TASK_WRITE_RESOURCES = frozenset({Resource.TASKS}) | TIMER_RESOURCES
"""Lo que cambia al cerrar u omitir una tarea: la propia lista y, ademas, los
cronometros -- ``complete_task``/``skip_task`` paran en la base cualquier
cronometro abierto de esa tarea, en el mismo commit."""


class ResourceCache:
    """Cuando se leyo cada ``Resource`` por ultima vez, y que lecturas hay en
    vuelo ahora mismo.

    Thread-safe con su propio lock, deliberadamente **independiente de**
    ``orchestrator.screen_lock``: aqui no se pinta ni se toca la pantalla, asi
    que consultar caducidad nunca compite con un repintado ni con una
    pulsacion. Ninguna operacion de esta clase hace red: el que pide los datos
    es el llamador, esto solo lleva la contabilidad.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fetched_at: dict[Resource, float] = {}  # monotonic de la ultima lectura con EXITO
        self._generation: dict[Resource, int] = {}  # sube con cada invalidacion, ver end_fetch
        self._in_flight: dict[Resource, int] = {}  # recurso -> generacion de la lectura en curso
        self._codes: dict[Resource, str | None] = {}  # ultimo codigo de error por recurso (None = ninguno)

    def stale(self, resources: frozenset[Resource], max_age: float) -> frozenset[Resource]:
        """De ``resources``, los que hay que volver a pedir.

        Un recurso esta caducado si nunca se leyo con exito, si se invalido
        tras una escritura, o si su ultima lectura tiene ya ``max_age``
        segundos. Con ``max_age = 0`` caducan todos (refresco forzado).
        """
        now = time.monotonic()
        with self._lock:
            return frozenset(
                resource
                for resource in resources
                if resource not in self._fetched_at or now - self._fetched_at[resource] >= max_age
            )

    def begin_fetch(self, resource: Resource) -> int | None:
        """Reserva una lectura de ``resource`` y devuelve su testigo.

        Returns:
            La generacion vigente, que habra que devolver a ``end_fetch``; o
            ``None`` si ya hay una lectura en curso de esos mismos datos
            (single-flight: dos entradas seguidas a la misma pantalla no
            disparan dos peticiones). Una lectura en vuelo de una generacion
            ANTERIOR no bloquea: sus datos ya son viejos (hubo una escritura
            entre medias), asi que hace falta otra.
        """
        with self._lock:
            generation = self._generation.get(resource, 0)
            if self._in_flight.get(resource) == generation:
                return None
            self._in_flight[resource] = generation
            return generation

    def end_fetch(self, resource: Resource, token: int, *, code: str | None) -> bool:
        """Cierra la lectura reservada con ``token`` y dice si puede aplicarse.

        Returns:
            ``True`` si el resultado sigue siendo valido; ``False`` si entre
            el ``begin_fetch`` y ahora hubo una invalidacion -- es decir, una
            escritura -- y por tanto estos datos ya nacen viejos. **Ese
            descarte es lo que garantiza la sincronia**: sin el, una lectura
            de tareas lanzada antes de cerrar una tarea devolveria esa tarea
            a la pantalla, deshaciendo visualmente lo que el usuario acaba de
            hacer. El llamador vuelve a pedir lo descartado.

        Un fallo (``code`` no vacio) se guarda pero **no** marca el recurso
        como leido: sigue caducado, asi que se reintenta en la siguiente
        navegacion en vez de esperar al ciclo periodico.
        """
        with self._lock:
            if self._in_flight.get(resource) == token:
                del self._in_flight[resource]
            if token != self._generation.get(resource, 0):
                return False
            self._codes[resource] = code
            if code is None:
                self._fetched_at[resource] = time.monotonic()
            return True

    def invalidate(self, resources: frozenset[Resource]) -> None:
        """Marca ``resources`` como caducados y descarta lo que este en vuelo.

        La llama toda escritura con el conjunto de lo que ha podido cambiar
        (ver ``TASK_WRITE_RESOURCES`` y compania). No pide nada: quien quiera
        los datos ya los pedira -- de inmediato si la pantalla los esta
        enseñando, o en la siguiente navegacion si no.
        """
        with self._lock:
            for resource in resources:
                self._generation[resource] = self._generation.get(resource, 0) + 1
                self._fetched_at.pop(resource, None)

    def code(self, resource: Resource) -> str | None:
        """Ultimo codigo de error de ``resource`` (``None`` si la ultima
        lectura fue bien). Lo pinta ``orchestrator._paint_current_screen``."""
        with self._lock:
            return self._codes.get(resource)

    def has_code(self, value: str, resources: frozenset[Resource]) -> bool:
        """Si alguno de ``resources`` arrastra el codigo ``value`` (p.ej.
        ``"NET"``, ver ``orchestrator._maybe_restore_project``)."""
        with self._lock:
            return any(self._codes.get(resource) == value for resource in resources)
