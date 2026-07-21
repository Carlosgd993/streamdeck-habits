# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un demonio Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos para TickTick. Corre en una Raspberry Pi, sondea la API abierta de TickTick, ilumina cada tecla en azul (pendiente) o verde (hecho hoy), y envía un checkin a TickTick al pulsar una tecla.

No hay build system, `requirements.txt` ni suite de tests — es un daemon desplegable (`orchestrator.py` + módulos) más dos scripts de smoke-test de hardware en `scripts/`.

## Cómo ejecutar

Las dependencias se instalan a mano en un venv. Los cuatro imports de terceros son:

```
pip install streamdeck python-dotenv requests Pillow
```

- `python orchestrator.py` — daemon principal (requiere una Stream Deck real conectada y un `.env` válido)
- `python scripts/test_hw.py` — smoke test de hardware: enumera el deck, imprime modelo/serie/firmware, registra pulsaciones crudas
- `python scripts/toggle_test.py` — smoke test visual: alterna cada tecla entre azul/verde al pulsarla, sin llamadas de red

Los tres esperan que la librería `StreamDeck` (python-elgato-streamdeck) tenga acceso al dispositivo USB físico, así que solo funcionan de forma significativa en el hardware objetivo (o con un deck conectado).

### Flujo de desarrollo

El daemon corre en una Raspberry Pi 3, que tiene este mismo repo clonado. El desarrollo (edición de código, con Claude Code) se hace en esta máquina; los cambios se envían luego a la RP3 y se prueban ahí — esta máquina de desarrollo no tiene Python ni el hardware de la Stream Deck, así que no se puede ejecutar ni probar nada localmente.

### Disposición en tiempo de ejecución (despliegue en Raspberry Pi)

`config.py` fija `BASE_DIR = "/opt/streamdeck-habits"` y el shebang de `orchestrator.py` apunta a `/opt/streamdeck-habits/venv/bin/python`. En la Pi desplegada se espera, junto al código, lo siguiente (todo gitignored salvo `.env.example`):
- `.env` — debe definir `TICKTICK_ACCESS_TOKEN` (cargado vía `python-dotenv` en `auth.get_token()`; se regenera a mano cuando caduca, no hay flujo de refresh)
- `habit_key_map.json` — mapeo persistido `habit_id -> key_index` (se crea/actualiza solo)
- `checkin_failures.log` — log JSON-lines de checkins fallidos hacia TickTick (errores de API, tecla en rojo)
- `device_errors.log` — log de texto plano de fallos del propio dispositivo Stream Deck (nunca se muestran en tecla)

Al desarrollar fuera de la Pi, trata `BASE_DIR` como fijo; no hay override por variable de entorno.

### Despliegue

`deploy/deploy.sh` hace `git pull`, copia `deploy/streamdeck-habits.service` a `/etc/systemd/system/`, y reinicia el servicio systemd (`ExecStart` apunta a `orchestrator.py`, `Restart=on-failure`).

## Arquitectura

El antiguo script único (`habits_display.py`) se dividió en módulos; `orchestrator.py` es el punto de entrada y coordina el resto:

- **`config.py`** — constantes compartidas: rutas (`BASE_DIR`, `ENV_FILE`, `MAP_FILE`, logs), `RESERVED_KEYS = {0, 5, 10}` / `AVAILABLE_KEYS` (15 teclas, 3 reservadas), `KEY_REFRESH = 0` (tecla de refresco manual), `REFRESH_SECONDS` (900s), colores `COLOR_*`.
- **`auth.py`** — `get_token()` carga `.env` y devuelve `TICKTICK_ACCESS_TOKEN`.
- **`ticktick_client.py`** — `TickTickClient` envuelve `requests` contra `api.ticktick.com/open/v1/habit*` (`get_habits`, `get_checkins_for`, `create_checkin`). Define jerarquía de excepciones propia (`TickTickAuthError` 401, `TickTickNetworkError`, `TickTickAPIError`) en vez de dejar pasar excepciones de `requests` o devolver `None`/`False`.
- **`habit_key_map.py`** — `load_map`/`save_map`/`update_mapping`: persiste el mapeo hábito→tecla en `habit_key_map.json`. Los hábitos nuevos reclaman la tecla libre más baja (nunca se reasignan); los hábitos que desaparecen de TickTick liberan su tecla automáticamente. Si no quedan teclas libres, el hábito nuevo se loguea como omitido (código `KFUL`) y no se muestra. **Importante**: `update_mapping` solo debe llamarse tras una lectura exitosa de `get_habits()` — nunca tras un fallo de red o auth, o se liberarían teclas de hábitos que en realidad siguen existiendo.
- **`habits/`** — jerarquía de tipos de hábito TickTick:
  - `base.py`: `Habit` (ABC) — `id`, `name`, `is_done_today`, `build_checkin_payload` abstracto.
  - `boolean.py`: `BooleanHabit` — checkin siempre `{value: 1.0, goal: 1.0}`.
  - `real.py`: `RealHabit` (hábitos cuantificables) — **esqueleto sin implementar**, lanza `NotImplementedError`.
  - `registry.py`: `build_habit(data)` — hoy enruta *todos* los hábitos, incluidos los de tipo `"Real"`, a `BooleanHabit` para no romper producción, hasta que `RealHabit` esté implementado. No asumas que un hábito `"Real"` usa `RealHabit`.
- **`error_codes.py`** — `CODES`: diccionario de códigos cortos (`T401`, `TNET`, `TERR`, `KFUL`) mostrados en tecla o en logs.
- **`health.py`** — `classify(exc)` decide si un fallo del ciclo se muestra en tecla (`"key"`, errores de la API de TickTick) o solo se loguea a fichero (`"file"`, fallos del propio Stream Deck); `log_failure` escribe a `checkin_failures.log`, `log_device_error` a `device_errors.log`.
- **`render_primitives.py`** — helpers Pillow de bajo nivel: `solid_tile` (color plano) y `text_tile` (texto envuelto y centrado sobre un color), ambos vía `PILHelper.create_image`/`to_native_format`.
- **`deck_renderer.py`** — helpers de alto nivel sobre una tecla o el deck completo: `render_habit`, `render_checkin_error`, `render_reserved`, `render_empty`, `render_all` (repinta las 15 teclas según mapeo/hábitos/checkins), `render_error_all` (pinta con el código de error todas las teclas actualmente mapeadas, para no dejar info obsoleta en pantalla cuando falla la lectura de hábitos o checkins).
- **`special_keys.py`** — `render_reserved_keys`: pinta las 3 teclas reservadas. `handle_key_press(key, refresh_event)`: la tecla `KEY_REFRESH` (0) activa `refresh_event` para forzar un refresco inmediato; las teclas 5 y 10 siguen siendo placeholder sin acción.
- **`deck_session.py`** — `DeckSession`: apertura/cierre/reconexión del dispositivo y brillo (`BRIGHTNESS = 60`). No sabe nada de hábitos ni de la API de TickTick. `reconnect()` se usa solo tras un fallo de dispositivo en marcha, nunca en el arranque inicial (eso lo hace `open()`, con reintentos).
- **`orchestrator.py`** — bucle principal (`main` → `refresh_cycle`, cada `REFRESH_SECONDS` o antes si se activa `refresh_event`): fetch hábitos → `update_mapping` → fetch checkins de hoy (`stamp` = `YYYYMMDD` local) → `render_all` → **re-registra** `deck.set_key_callback(...)` con un closure fresco sobre el mapeo actual. Esto significa que el mapeo tecla→hábito usado al pulsar solo es tan reciente como el último ciclo de refresco, no en vivo. El estado pasado al callback usa wrappers dict de una entrada (`habits_ref = {"value": ...}`, `done_ids_ref`) para que el closure observe actualizaciones de ciclos posteriores — aunque el callback igualmente se recrea cada ciclo. El bucle principal espera con `refresh_event.wait(timeout=REFRESH_SECONDS)` en vez de `time.sleep`: pulsar `KEY_REFRESH` (tecla 0, vía `special_keys.handle_key_press`) activa ese mismo `Event` desde el hilo de callbacks del Stream Deck, despertando el bucle principal para que ejecute `refresh_cycle` de inmediato — la API sigue siendo la única fuente de verdad, solo cambia cuándo se consulta. `Event.set()` es idempotente, así que pulsaciones repetidas mientras ya hay un refresco en curso no encolan refrescos extra. `make_key_callback` evita checkins duplicados por hábito con un `pending_requests` set global + `state_lock` (una doble pulsación mientras hay una petición en vuelo se ignora). Al éxito la tecla se pone verde de inmediato (actualización optimista local, sin refetch); al fallo se pinta en rojo con el código de error y el fallo se añade a `checkin_failures.log`. Cualquier excepción que no sea de la API de TickTick (ya gestionadas dentro de `refresh_cycle`) se trata como error de dispositivo: nunca se muestra en tecla, y dispara `session.reconnect()`.

## Convenciones

- Los mensajes de log de cara al usuario y los comentarios de código están en español; mantén los nuevos consistentes con eso.
- Todos los `print` usan `flush=True` porque esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).
