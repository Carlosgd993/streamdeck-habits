"""Proyectos fijados como boton directo en el menu principal (ver
``core.screens.ScreenKind.PROJECT_OPTIONS``): una preferencia del propio deck,
activable/desactivable desde el hardware, no un dato de ``../habits-core``.

Mismo patron que ``core.pinned_sections`` con ``pinned_sections.json`` (que a
su vez sigue el de ``core.key_map`` con ``habit_key_map.json``): persistido en
``config.PINNED_PROJECTS_FILE`` como una lista JSON, cargado entero en memoria
al arrancar el daemon y reescrito solo al cambiar. Los nombres se normalizan
(``strip().lower()``) al guardar y comparar, igual que
``core.screens._project_page`` ya hace contra ``Task.project_name`` -- para
que un proyecto no se desdoble en dos entradas por una diferencia de
mayusculas/espacios entre el nombre que trajo una tarea y el que se guardo al
fijarlo. Modulo hermano separado de ``core.pinned_sections`` a proposito, no
una abstraccion compartida -- mismo criterio que ``HABIT_OPTIONS_LAYOUT``/
``TASK_OPTIONS_LAYOUT`` en ``core.screens``: dominios hoy identicos que
podrian divergir."""

from __future__ import annotations

import json
import os

from config import PINNED_PROJECTS_FILE


def _normalize(name: str) -> str:
    return name.strip().lower()


def load() -> frozenset[str]:
    """Carga el conjunto de proyectos fijados, o vacio si no existe el fichero."""
    if os.path.exists(PINNED_PROJECTS_FILE):
        with open(PINNED_PROJECTS_FILE) as f:
            return frozenset(_normalize(name) for name in json.load(f))
    return frozenset()


def _save(names: frozenset[str]) -> None:
    with open(PINNED_PROJECTS_FILE, "w") as f:
        json.dump(sorted(names), f, indent=2)


def toggle(project_name: str, pinned: frozenset[str]) -> frozenset[str]:
    """Alterna si ``project_name`` esta fijado, persiste el resultado a disco
    y devuelve el conjunto ya actualizado (``pinned`` no se muta in situ: es
    un ``frozenset``, igual que el resto de este modulo).

    Si ``project_name`` esta vacio no hace nada (defensivo: no deberia llegar
    aqui vacio, ver ``core.screens.ScreenState.entry_project_name``)."""
    key = _normalize(project_name)
    if not key:
        return pinned
    updated = (pinned - {key}) if key in pinned else (pinned | {key})
    _save(updated)
    return updated
