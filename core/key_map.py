"""Reparto de teclas: el mapeo habito -> tecla persistido en habit_key_map.json
y el mapeo tarea -> tecla, que es volatil y se recalcula en cada ciclo."""

from __future__ import annotations

import json
import os

from config import AVAILABLE_KEYS, MAP_FILE
from core.error_codes import CODES
from provider.base import Habit, Task


def load_map() -> dict[str, int]:
    """Carga el mapeo habito -> tecla persistido, o un dict vacio si no existe."""
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE) as f:
            return json.load(f)
    return {}


def save_map(mapping: dict[str, int]) -> None:
    """Escribe el mapeo habito -> tecla a disco en formato JSON."""
    with open(MAP_FILE, "w") as f:
        json.dump(mapping, f, indent=2)


def update_mapping(habits: list[Habit], mapping: dict[str, int]) -> dict[str, int]:
    """Reconcilia el mapeo habito -> tecla con la lista actual de habitos.

    Asigna la primera tecla libre a cada habito nuevo (nunca la reasigna) y
    libera las teclas de habitos que ya no aparecen en ``habits``. Si no queda
    ninguna tecla libre, el habito nuevo se registra como omitido (codigo
    ``KFUL``) y no recibe tecla. Persiste el resultado si hubo cambios.

    IMPORTANTE: solo debe llamarse tras una lectura de ``get_habits()`` exitosa
    (sin excepcion) -- nunca tras un fallo de red o de autenticacion, o se
    liberarian teclas de habitos que en realidad siguen existiendo.

    Args:
        habits: Habitos actuales devueltos por el proveedor.
        mapping: Mapeo actual habito -> tecla, modificado in situ.

    Returns:
        El mismo ``mapping`` ya reconciliado.
    """
    changed = False

    current_ids = {h.id for h in habits}
    stale_ids = [hid for hid in mapping if hid not in current_ids]
    for hid in stale_ids:
        key = mapping.pop(hid)
        changed = True
        print(f"Habito {hid} ya no existe, se libera la tecla {key}", flush=True)

    used_keys = set(mapping.values())
    free_keys = [k for k in AVAILABLE_KEYS if k not in used_keys]

    known_ids = set(mapping.keys())
    new_habits = [h for h in habits if h.id not in known_ids]
    new_habits.sort(key=lambda h: (h.order, h.id))

    for habit in new_habits:
        if not free_keys:
            print(f"[KFUL] {CODES['KFUL']}: {habit.name}", flush=True)
            continue
        key = free_keys.pop(0)
        mapping[habit.id] = key
        changed = True
        print(f"Nuevo habito '{habit.name}' -> tecla {key}", flush=True)

    if changed:
        save_map(mapping)
    return mapping


def assign_task_keys(tasks: list[Task], habit_mapping: dict[str, int]) -> dict[str, int]:
    """Reparte entre las tareas las teclas que los habitos dejan libres.

    A diferencia del mapeo de habitos, este **no se persiste ni se reconcilia**:
    las tareas son volatiles (nacen y se cierran a lo largo del dia), asi que
    cada ciclo se reparten de cero en el orden en que llegan -- el proveedor ya
    las devuelve por prioridad. Los habitos tienen preferencia: una tarea nunca
    ocupa una tecla asignada a un habito, aunque ese habito lleve dias sin usarse.

    Las tareas que no caben simplemente no reciben tecla (codigo ``KFUL``), igual
    que un habito nuevo cuando el deck esta lleno.

    Args:
        tasks: Tareas pendientes, en el orden en que deben ocupar las teclas.
        habit_mapping: Mapeo habito -> tecla vigente, cuyas teclas quedan vetadas.

    Returns:
        Un mapeo nuevo ``task_id -> tecla`` con las tareas que cupieron.
    """
    used_keys = set(habit_mapping.values())
    free_keys = [k for k in AVAILABLE_KEYS if k not in used_keys]

    task_keys: dict[str, int] = {}
    for task in tasks:
        if not free_keys:
            print(f"[KFUL] {CODES['KFUL']}: tarea '{task.title}'", flush=True)
            continue
        task_keys[task.id] = free_keys.pop(0)
    return task_keys
