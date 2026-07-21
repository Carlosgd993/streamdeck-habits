# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small Python daemon that turns a physical Elgato Stream Deck into a habit-tracking remote for TickTick. It runs on a Raspberry Pi, polls the TickTick Open API for the user's habits, lights up each key blue (pending) or green (done today), and posts a check-in to TickTick when a key is pressed.

There is no build system, package manifest, or test suite — this is a single deployable script plus two hardware smoke-test scripts.

## Running

No `requirements.txt` exists yet; dependencies are installed ad hoc into a venv. The four third-party imports are:

```
pip install streamdeck python-dotenv requests Pillow
```

- `python habits_display.py` — main daemon (requires a real Stream Deck plugged in and a valid `.env`)
- `python test_hw.py` — hardware smoke test: enumerates the deck, prints model/serial/firmware, logs raw key presses
- `python toggle_test.py` — visual smoke test: toggles each key between blue/green on press, no network calls

All three expect the `StreamDeck` library (python-elgato-streamdeck) to have access to the physical USB device, so they only run meaningfully on the target hardware (or with a device attached).

### Runtime layout (Raspberry Pi deployment)

`habits_display.py` hardcodes `BASE_DIR = "/opt/streamdeck-habits"` and its shebang points at `/opt/streamdeck-habits/venv/bin/python`. On the deployed Pi it expects, alongside the script:
- `.env` — must define `TICKTICK_ACCESS_TOKEN` (loaded via `python-dotenv`; regenerated manually when it expires, there is no refresh flow)
- `habit_key_map.json` — persisted `habit_id -> key_index` mapping (auto-created/updated, gitignored)
- `checkin_failures.log` — append-only JSON-lines log of failed check-in attempts (gitignored)

When developing locally off the Pi, treat `BASE_DIR` as effectively fixed; there's no env override for it.

## Architecture (`habits_display.py`)

Single-file, single-process, no classes — a few closures over shared mutable state.

- **Key layout**: 15-key deck. Keys `{0, 5, 10}` are `RESERVED_KEYS` (always rendered dark, never assigned a habit). The remaining 12 (`AVAILABLE_KEYS`) get habits assigned first-come-first-served as new habits appear.
- **Habit → key mapping** (`load_map`/`save_map`/`update_mapping`): persisted in `habit_key_map.json`. New habits (by TickTick habit id, not seen in the map yet) claim the lowest free key; existing mappings are never reassigned or freed automatically. If all keys are taken, new habits are logged as skipped, not shown.
- **Poll loop** (`main` → `refresh_cycle`): runs every `REFRESH_SECONDS` (900s). Each cycle: fetch all habits → update the key mapping → fetch today's check-ins (`stamp` = local `YYYYMMDD` int) → re-render every key → **re-register** `deck.set_key_callback(...)` with a fresh closure captured over the current mapping. This means the key→habit mapping used by press handling is only as fresh as the last refresh cycle, not live.
- **State passed into the callback** uses one-entry dict wrappers (`habits_ref = {"value": ...}`, `done_ids_ref = {"value": ...}`) specifically so the callback closure can observe updates made by later `refresh_cycle()` calls without being re-created — but the callback itself *is* re-created each cycle anyway (see above), and `habits_ref`/`done_ids_ref` are reassigned via `nonlocal`-free dict mutation from `main`.
- **Key press handling** (`make_key_callback`): guards against duplicate in-flight check-ins per habit via a global `pending_requests` set + `state_lock` (a double key-press while a request is in flight is ignored). On success the key turns green immediately (optimistic local update, no re-fetch); on failure the key is drawn red with the habit name and the failure is appended to `checkin_failures.log`.
- **Rendering** (`make_key_image`/`render_key`/`render_all`): builds key images with Pillow (`PILHelper.create_image` → draw wrapped/centered text → `PILHelper.to_native_format`). Colors are the `COLOR_*` constants near the top of the file — habit pending/done, error, reserved, empty.
- **TickTick API calls** (`fetch_habits`, `fetch_today_checkins`, `send_checkin`) are thin `requests` wrappers against `api.ticktick.com/open/v1/habit*`, auth via `Authorization: Bearer <TICKTICK_ACCESS_TOKEN>`. A 401 on `fetch_habits` is treated as an expired/invalid token and logged (no automatic re-auth — it just skips that cycle).

## Conventions

- User-facing log messages and code comments are in Spanish; keep new ones consistent with that.
- All prints use `flush=True` since this runs as an unbuffered background service (journald/systemd-style logging).
