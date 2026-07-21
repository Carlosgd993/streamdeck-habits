import json
import os

from config import AVAILABLE_KEYS, MAP_FILE
from error_codes import CODES


def load_map():
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE) as f:
            return json.load(f)
    return {}


def save_map(mapping):
    with open(MAP_FILE, "w") as f:
        json.dump(mapping, f, indent=2)


def update_mapping(habits, mapping):
    """Asigna la primera tecla libre a cada habito nuevo (nunca la reasigna) y
    libera las teclas de habitos que ya no aparecen en 'habits'.

    IMPORTANTE: solo debe llamarse tras una lectura de get_habits() exitosa
    (sin excepcion) -- nunca tras un fallo de red o de autenticacion, o se
    liberarian teclas de habitos que en realidad siguen existiendo.
    """
    changed = False

    current_ids = {h["id"] for h in habits}
    stale_ids = [hid for hid in mapping if hid not in current_ids]
    for hid in stale_ids:
        key = mapping.pop(hid)
        changed = True
        print(f"Habito {hid} ya no existe, se libera la tecla {key}", flush=True)

    used_keys = set(mapping.values())
    free_keys = [k for k in AVAILABLE_KEYS if k not in used_keys]

    known_ids = set(mapping.keys())
    new_habits = [h for h in habits if h["id"] not in known_ids]
    new_habits.sort(key=lambda h: h.get("sortOrder", h["id"]))

    for habit in new_habits:
        if not free_keys:
            print(f"[KFUL] {CODES['KFUL']}: {habit['name']}", flush=True)
            continue
        key = free_keys.pop(0)
        mapping[habit["id"]] = key
        changed = True
        print(f"Nuevo habito '{habit['name']}' -> tecla {key}", flush=True)

    if changed:
        save_map(mapping)
    return mapping
