"""Secciones de habitos fijadas como boton directo en el menu principal (ver
``core.screens.ScreenKind.SECTION_OPTIONS``): una preferencia del propio deck,
activable/desactivable desde el hardware, no un dato de ``../habits-core``.

Mismo patron que ``core.key_map`` con ``habit_key_map.json``
(``config.MAP_FILE``): persistido en ``config.PINNED_SECTIONS_FILE`` como una
lista JSON, cargado entero en memoria al arrancar el daemon y reescrito solo
al cambiar. Los nombres se normalizan (``strip().lower()``) al guardar y
comparar, igual que ``core.screens._section_page`` ya hace contra
``Habit.section_name`` -- para que una sección no se desdoble en dos entradas
por una diferencia de mayusculas/espacios entre el nombre que trajo un habito
y el que se guardo al fijarla."""

from __future__ import annotations

import json
import os

from config import PINNED_SECTIONS_FILE


def _normalize(name: str) -> str:
    return name.strip().lower()


def load() -> frozenset[str]:
    """Carga el conjunto de secciones fijadas, o vacio si no existe el fichero."""
    if os.path.exists(PINNED_SECTIONS_FILE):
        with open(PINNED_SECTIONS_FILE) as f:
            return frozenset(_normalize(name) for name in json.load(f))
    return frozenset()


def _save(names: frozenset[str]) -> None:
    with open(PINNED_SECTIONS_FILE, "w") as f:
        json.dump(sorted(names), f, indent=2)


def toggle(section_name: str, pinned: frozenset[str]) -> frozenset[str]:
    """Alterna si ``section_name`` esta fijada, persiste el resultado a disco
    y devuelve el conjunto ya actualizado (``pinned`` no se muta in situ: es
    un ``frozenset``, igual que el resto de este modulo).

    Si ``section_name`` esta vacio no hace nada (defensivo: no deberia llegar
    aqui vacio, ver ``core.screens.ScreenState.entry_section_name``)."""
    key = _normalize(section_name)
    if not key:
        return pinned
    updated = (pinned - {key}) if key in pinned else (pinned | {key})
    _save(updated)
    return updated
