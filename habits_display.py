#!/opt/streamdeck-habits/venv/bin/python
import json
import os
import sys
import time
import textwrap
import threading
from datetime import datetime

import requests
from dotenv import load_dotenv
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from PIL import ImageDraw, ImageFont

BASE_DIR = "/opt/streamdeck-habits"
ENV_FILE = os.path.join(BASE_DIR, ".env")
MAP_FILE = os.path.join(BASE_DIR, "habit_key_map.json")
FAIL_LOG = os.path.join(BASE_DIR, "checkin_failures.log")

RESERVED_KEYS = {0, 5, 10}
ALL_KEYS = set(range(15))
AVAILABLE_KEYS = sorted(ALL_KEYS - RESERVED_KEYS)

REFRESH_SECONDS = 900  # 15 min

COLOR_HABIT_PENDING = (40, 60, 200)   # azul: hoy no hecho
COLOR_HABIT_DONE = (30, 200, 60)      # verde: hoy hecho
COLOR_ERROR = (200, 40, 40)           # rojo: fallo al enviar
COLOR_RESERVED = (25, 25, 25)
COLOR_EMPTY = (0, 0, 0)

load_dotenv(ENV_FILE)
TOKEN = os.environ.get("TICKTICK_ACCESS_TOKEN")

state_lock = threading.Lock()
pending_requests = set()  # habit_ids con checkin en vuelo, para no duplicar por doble pulsación


def api_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def today_stamp():
    now = datetime.now()
    return int(now.strftime("%Y%m%d"))


def fetch_habits():
    resp = requests.get(
        "https://api.ticktick.com/open/v1/habit",
        headers=api_headers(),
        timeout=10,
    )
    if resp.status_code == 401:
        print("Token invalido o expirado. Hay que regenerarlo manualmente.", flush=True)
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_today_checkins(habit_ids, stamp):
    """Devuelve el set de habit_ids que YA tienen checkin para 'stamp'."""
    if not habit_ids:
        return set()
    resp = requests.get(
        "https://api.ticktick.com/open/v1/habit/checkins",
        headers=api_headers(),
        params={"habitIds": ",".join(habit_ids), "from": stamp, "to": stamp},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"No se pudieron leer checkins de hoy: {resp.status_code}", flush=True)
        return set()
    done = set()
    for entry in resp.json():
        if entry.get("checkins"):
            done.add(entry["habitId"])
    return done


def send_checkin(habit_id, stamp):
    resp = requests.post(
        f"https://api.ticktick.com/open/v1/habit/{habit_id}/checkin",
        headers={**api_headers(), "Content-Type": "application/json"},
        json={"stamp": stamp, "value": 1.0, "goal": 1.0},
        timeout=10,
    )
    return resp.status_code in (200, 201)


def log_failure(habit_id, detail):
    with open(FAIL_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "habit_id": habit_id,
            "detail": detail,
        }) + "\n")


def load_map():
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE) as f:
            return json.load(f)
    return {}


def save_map(mapping):
    with open(MAP_FILE, "w") as f:
        json.dump(mapping, f, indent=2)


def update_mapping(habits, mapping):
    used_keys = set(mapping.values())
    free_keys = [k for k in AVAILABLE_KEYS if k not in used_keys]

    known_ids = set(mapping.keys())
    new_habits = [h for h in habits if h["id"] not in known_ids]
    new_habits.sort(key=lambda h: h.get("sortOrder", h["id"]))

    changed = False
    for habit in new_habits:
        if not free_keys:
            print(f"Sin teclas libres, no se puede mostrar: {habit['name']}", flush=True)
            continue
        key = free_keys.pop(0)
        mapping[habit["id"]] = key
        changed = True
        print(f"Nuevo habito '{habit['name']}' -> tecla {key}", flush=True)

    if changed:
        save_map(mapping)
    return mapping


def make_key_image(deck, color, text=None):
    image = PILHelper.create_image(deck, background=color)
    if text:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        lines = textwrap.wrap(text, width=10)
        w, h = image.size
        line_h = 12
        total_h = len(lines) * line_h
        y = (h - total_h) // 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            draw.text(((w - line_w) // 2, y), line, font=font, fill=(255, 255, 255))
            y += line_h
    return PILHelper.to_native_format(deck, image)


def render_key(deck, key, habit, done):
    if habit is None:
        deck.set_key_image(key, make_key_image(deck, COLOR_EMPTY))
        return
    color = COLOR_HABIT_DONE if done else COLOR_HABIT_PENDING
    deck.set_key_image(key, make_key_image(deck, color, text=habit["name"]))


def render_all(deck, habits, mapping, done_ids):
    habits_by_id = {h["id"]: h for h in habits}
    key_to_habit_id = {k: hid for hid, k in mapping.items()}
    for key in range(deck.key_count()):
        if key in RESERVED_KEYS:
            deck.set_key_image(key, make_key_image(deck, COLOR_RESERVED))
            continue
        habit_id = key_to_habit_id.get(key)
        habit = habits_by_id.get(habit_id) if habit_id else None
        render_key(deck, key, habit, habit_id in done_ids if habit_id else False)


def make_key_callback(deck, mapping, habits_ref, done_ids_ref):
    key_to_habit_id = {k: hid for hid, k in mapping.items()}

    def on_key_change(deck, key, pressed):
        if not pressed:
            return
        habit_id = key_to_habit_id.get(key)
        if habit_id is None:
            return  # tecla reservada o sin habito asignado todavia

        with state_lock:
            if habit_id in pending_requests:
                return  # ya hay una peticion en vuelo para este habito
            pending_requests.add(habit_id)

        habits_by_id = {h["id"]: h for h in habits_ref["value"]}
        habit = habits_by_id.get(habit_id)
        stamp = today_stamp()

        try:
            ok = send_checkin(habit_id, stamp)
        except requests.RequestException as exc:
            ok = False
            log_failure(habit_id, str(exc))

        if ok:
            done_ids_ref["value"].add(habit_id)
            render_key(deck, key, habit, done=True)
            print(f"Checkin OK: {habit['name'] if habit else habit_id} ({stamp})", flush=True)
        else:
            log_failure(habit_id, f"POST checkin fallo, status no OK, stamp={stamp}")
            render_key(deck, key, habit, done=False)
            deck.set_key_image(key, make_key_image(deck, COLOR_ERROR, text=habit["name"] if habit else "?"))
            print(f"Checkin FALLO: {habit_id} ({stamp}) - ver {FAIL_LOG}", flush=True)

        with state_lock:
            pending_requests.discard(habit_id)

    return on_key_change


def find_deck(retries=30, delay=2):
    for i in range(retries):
        decks = DeviceManager().enumerate()
        if decks:
            return decks[0]
        print(f"Sin Stream Deck detectada, reintento {i + 1}/{retries}", flush=True)
        time.sleep(delay)
    return None


def main():
    if not TOKEN:
        print(f"Falta TICKTICK_ACCESS_TOKEN en {ENV_FILE}", flush=True)
        sys.exit(1)

    print(f"Fecha/hora local interpretada al arrancar: {datetime.now().isoformat()}", flush=True)

    deck = find_deck()
    if deck is None:
        print("No se encontro la Stream Deck tras los reintentos.", flush=True)
        sys.exit(1)

    deck.open()
    deck.reset()
    deck.set_brightness(60)

    mapping = load_map()
    habits_ref = {"value": []}
    done_ids_ref = {"value": set()}

    def refresh_cycle():
        habits = fetch_habits()
        if habits is None:
            return
        habits_ref["value"] = habits
        nonlocal mapping
        mapping = update_mapping(habits, mapping)
        stamp = today_stamp()
        done_ids_ref["value"] = fetch_today_checkins(list(mapping.keys()), stamp)
        render_all(deck, habits, mapping, done_ids_ref["value"])
        deck.set_key_callback(make_key_callback(deck, mapping, habits_ref, done_ids_ref))

    try:
        while True:
            refresh_cycle()
            time.sleep(REFRESH_SECONDS)
    finally:
        deck.reset()
        deck.close()


if __name__ == "__main__":
    main()
