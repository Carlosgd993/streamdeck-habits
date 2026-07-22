# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un demonio Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos para TickTick. Corre en una Raspberry Pi 3, sondea la API abierta de TickTick, ilumina cada tecla en azul (pendiente) o verde (hecho hoy), y envía un checkin a TickTick al pulsar una tecla.

Es un daemon desplegable (`orchestrator.py` + módulos) más dos scripts de smoke-test de hardware en `scripts/`. Hay un `pyproject.toml` con configuración de herramientas (ruff, mypy) y metadatos del proyecto, pero **las dependencias se instalan a mano** (no hay `requirements.txt` ni lockfile) y **no hay suite de tests automatizada** — la verificación se hace ejecutando en la Pi (ver [Verificación previa al despliegue](#verificación-previa-al-despliegue)).

## Cómo ejecutar

Las dependencias se instalan a mano en un venv. Los cuatro imports de terceros son:

```
pip install streamdeck python-dotenv requests Pillow
```

Para pintar el emoji del nombre de un hábito como icono a color en la tecla hace falta además la fuente `fonts-noto-color-emoji` instalada a nivel de sistema en la Pi (paquete Debian, no es un paquete de Python): `sudo apt install fonts-noto-color-emoji` (requiere contraseña de sudo interactiva — no se puede instalar por SSH no interactivo desde esta máquina). Si no está instalada, `render_primitives._emoji_font()` devuelve `None` y la tecla simplemente no pinta el icono (se degrada, no falla).

- `python orchestrator.py` — daemon principal (requiere una Stream Deck real conectada y un `.env` válido)
- `python scripts/test_hw.py` — smoke test de hardware: enumera el deck, imprime modelo/serie/firmware, registra pulsaciones crudas
- `python scripts/toggle_test.py` — smoke test visual: alterna cada tecla entre azul/verde al pulsarla, sin llamadas de red

Los tres esperan que la librería `StreamDeck` (python-elgato-streamdeck) tenga acceso al dispositivo USB físico, así que solo funcionan de forma significativa en el hardware objetivo (o con un deck conectado).

### Flujo de desarrollo

El daemon corre en una Raspberry Pi 3, que tiene este mismo repo clonado. El desarrollo (edición de código, con Claude Code) se hace en esta máquina; los cambios se envían luego a la RP3 y se prueban ahí. **Esta máquina de desarrollo no tiene Python usable** (solo un alias stub de la Microsoft Store que no ejecuta nada) **ni el hardware de la Stream Deck**, así que no se puede ejecutar ni probar nada localmente — hay que acceder a la Raspberry Pi para ello (ver más abajo cómo hacerlo por SSH sin depender del usuario).

### Verificación previa al despliegue

Aunque no se puede *ejecutar* el daemon fuera de la Pi, sí se puede **validar sintaxis e imports** de cualquier cambio antes de desplegar, sin tocar el servicio en marcha, copiando los `.py` a un directorio temporal en la Pi y compilándolos/importándolos con el venv real (Python 3.13.5, con todas las dependencias instaladas). Es lo más parecido a un test disponible desde esta máquina:

```bash
# Chequeo de sintaxis (byte-compila, no ejecuta nada):
tar cf - $(find . -name '*.py' -not -path './venv/*') | ssh admin@RP3-MotoComm-1.local \
  'd=$(mktemp -d); tar xf - -C "$d"; /opt/streamdeck-habits/venv/bin/python -m compileall -q "$d" && echo SYNTAX_OK; rc=$?; rm -rf "$d"; exit $rc'

# Chequeo más fuerte: importar los módulos (detecta imports circulares, NameError, etc.).
# Seguro porque main() está bajo `if __name__ == "__main__"`, así que importar no arranca el daemon
# ni necesita un deck conectado (importar DeviceManager no requiere hardware):
tar cf - $(find . -name '*.py' -not -path './venv/*') | ssh admin@RP3-MotoComm-1.local \
  'd=$(mktemp -d); tar xf - -C "$d"; cd "$d"; /opt/streamdeck-habits/venv/bin/python -c "import orchestrator, deck_renderer, special_keys, habits.registry, health, habit_key_map, ticktick_client, auth, deck_session; print(\"IMPORTS_OK\")"; rc=$?; cd /; rm -rf "$d"; exit $rc'
```

### Probar código sin mergear (`deploy.sh --test`)

Para validar en la Pi un cambio del árbol de trabajo local **antes de commitear/mergear a `main`**, sin que `git pull` machaque el código de prueba:

1. **Copiar** el árbol de trabajo (solo versionado + nuevos no-ignorados; nunca `.env`, `venv/`, logs ni `habit_key_map.json`) sobre `/opt/streamdeck-habits`:

   ```bash
   tar cf - $(git ls-files -c -o --exclude-standard) | ssh admin@RP3-MotoComm-1.local 'tar xf - -C /opt/streamdeck-habits'
   ```

   Deja el árbol git de la Pi "sucio" respecto a `main`, pero es reversible. Al venir de Windows los ficheros llegan con CRLF (inocuo para Python; `.gitattributes` fuerza LF en los `.sh`/`.service` para que los scripts no se rompan).

2. **Reiniciar** con el código copiado, sin `git pull` (modo `--test` de `deploy.sh`):

   ```bash
   ssh admin@RP3-MotoComm-1.local 'bash /opt/streamdeck-habits/deploy/deploy.sh --test'
   ```

3. **Observar** que arranca limpio (PID nuevo estable, sin errores en journal ni en `checkin_failures.log`/`device_errors.log`) y confirmar el comportamiento en el hardware.

4. **Si va bien** → commit + push a `main`; luego en la Pi `git -C /opt/streamdeck-habits fetch && git -C /opt/streamdeck-habits reset --hard origin/main` (deja `main` limpio con LF) y desplegar normal (`deploy/deploy.sh`, ver más abajo por qué no usar el alias `habits-update` por SSH).
   **Si va mal** → en la Pi `git -C /opt/streamdeck-habits checkout -- .` (restaura `main`) y `deploy/deploy.sh --test`. Si el código de prueba llegó a crashear en bucle y systemd lo dejó parado, arrancarlo de nuevo sí necesita sudo: `sudo systemctl start streamdeck-habits.service`.

### Estilo de código y herramientas

El estándar de estilo está en `pyproject.toml` (ver también `.claude/SKILL_Python_Code_Style_&_Documentation.md`):

- **`ruff`** — lint + formato (línea 120, comillas dobles, isort/pyupgrade/bugbear/simplify): `ruff check --fix .` y `ruff format .`
- **`mypy`** — comprobación de tipos (`target py313`; `StreamDeck.*` con `ignore_missing_imports` porque no publica stubs): `mypy .`
- **Type hints** en todas las APIs públicas, con `from __future__ import annotations` al principio de cada módulo (evaluación diferida: la anotación nunca se evalúa en runtime, así que es segura aunque un tipo apunte a algo no importado).
- **Docstrings estilo Google** (Args/Returns/Raises) en clases y funciones públicas.

### Disposición en tiempo de ejecución (despliegue en Raspberry Pi)

`config.py` fija `BASE_DIR = "/opt/streamdeck-habits"` y el shebang de `orchestrator.py` apunta a `/opt/streamdeck-habits/venv/bin/python` (Python 3.13.5). En la Pi desplegada se espera, junto al código, lo siguiente (todo gitignored salvo `.env.example`):
- `.env` — debe definir `TICKTICK_ACCESS_TOKEN` (cargado vía `python-dotenv` en `auth.get_token()`; se regenera a mano cuando caduca, no hay flujo de refresh)
- `habit_key_map.json` — mapeo persistido `habit_id -> key_index` (se crea/actualiza solo)
- `checkin_failures.log` — log JSON-lines de checkins fallidos hacia TickTick (errores de API, tecla en rojo)
- `device_errors.log` — log de texto plano de fallos del propio dispositivo Stream Deck (nunca se muestran en tecla)

Al desarrollar fuera de la Pi, trata `BASE_DIR` como fijo; no hay override por variable de entorno.

### Despliegue

`deploy/deploy.sh` hace `git pull` y reinicia el servicio (matando el proceso principal, que corre como `admin`, para que systemd lo relance por `Restart=on-failure` con el código nuevo — **sin sudo**, así que corre por SSH de forma no interactiva). Acepta `--test`, que hace lo mismo pero **sin `git pull`** (ver [Probar código sin mergear](#probar-código-sin-mergear-deploysh---test)). No instala cambios de la unit de systemd (`deploy/streamdeck-habits.service`); si cambias ese fichero, cópialo a mano a `/etc/systemd/system/` con `sudo` + `daemon-reload` + `restart`.

### Acceso a la Raspberry Pi

Hay acceso SSH sin contraseña (clave pública ya autorizada) desde esta máquina de desarrollo a la Pi: `ssh admin@RP3-MotoComm-1.local`. Esto permite lanzar comandos directamente en la Pi (revisar logs, estado del servicio, reiniciar, desplegar, verificar cambios) sin depender del usuario para ejecutarlos a mano.

La resolución del hostname `.local` (mDNS) es a veces intermitente: si un comando falla con "Could not resolve hostname", reintenta antes de darlo por caído.

Tras hacer cambios en el código y subirlos (push), la forma más rápida de desplegar es invocar `deploy/deploy.sh` directamente:

```
ssh admin@RP3-MotoComm-1.local "bash /opt/streamdeck-habits/deploy/deploy.sh"
```

Existe un alias `habits-update` en `~/.bashrc` para uso interactivo, pero **no funciona desde `ssh host "habits-update"`**: el `.bashrc` de Debian corta la ejecución al inicio si el shell no es interactivo (guarda `case $- in *i*) ;; *) return;; esac`), así que un comando SSH no interactivo nunca llega a definir el alias y falla con `command not found`. Usa siempre la ruta completa al script.

Como con cualquier acción que afecte al servicio en producción, confirma con el usuario antes de ejecutar el despliegue o reiniciar el servicio, salvo que ya lo haya pedido explícitamente en el mismo turno. La verificación previa al despliegue (compilar/importar en un directorio temporal) no afecta al servicio y no necesita confirmación.

## Arquitectura

El antiguo script único (`habits_display.py`) se dividió en módulos; `orchestrator.py` es el punto de entrada y coordina el resto:

- **`config.py`** — constantes compartidas: rutas (`BASE_DIR`, `ENV_FILE`, `MAP_FILE`, logs), `RESERVED_KEYS = {0, 5, 10}` / `AVAILABLE_KEYS` (15 teclas, 3 reservadas), `KEY_REFRESH = 0` (tecla de refresco manual), `REFRESH_SECONDS` (900s), colores `COLOR_*`.
- **`auth.py`** — `get_token()` carga `.env` y devuelve `TICKTICK_ACCESS_TOKEN`.
- **`ticktick_client.py`** — `TickTickClient` envuelve `requests` contra `api.ticktick.com/open/v1/habit*` (`get_habits`, `get_checkin_progress_for`, `create_checkin`). `get_checkin_progress_for` devuelve, por habito, el `value`/`goal` del checkin de un día concreto (el `goal` es el vigente cuando se registró ese checkin, no necesariamente el actual); el orquestador deriva de ahí tanto `done_ids` (`value >= goal`) como el progreso acumulado. El checkin de TickTick **no es incremental**: cada `POST .../checkin` hace upsert del `value` total de ese `stamp`, así que sumar `+step` exige conocer el `value` de hoy y reenviarlo ya incrementado (ver `habits/real.py`). Define jerarquía de excepciones propia (`TickTickAuthError` 401, `TickTickNetworkError`, `TickTickAPIError`) en vez de dejar pasar excepciones de `requests` o devolver `None`/`False`.
- **`habit_key_map.py`** — `load_map`/`save_map`/`update_mapping`: persiste el mapeo hábito→tecla en `habit_key_map.json`. Los hábitos nuevos reclaman la tecla libre más baja (nunca se reasignan); los hábitos que desaparecen de TickTick liberan su tecla automáticamente. Si no quedan teclas libres, el hábito nuevo se loguea como omitido (código `KFUL`) y no se muestra. **Importante**: `update_mapping` solo debe llamarse tras una lectura exitosa de `get_habits()` — nunca tras un fallo de red o auth, o se liberarían teclas de hábitos que en realidad siguen existiendo.
- **`emoji_utils.py`** — `extract_emoji(text)`: separa el primer emoji (o secuencia emoji, incluyendo variation selector y ZWJ) de una cadena, devolviendo `(emoji, resto_sin_emoji_ni_espacios_sobrantes)`. Sin dependencias de Pillow/StreamDeck para poder usarse tanto desde `habits/` (lógica de dominio) como desde `render_primitives.py` (dibujado).
- **`habits/`** — jerarquía de tipos de hábito TickTick:
  - `base.py`: `Habit` (ABC) — `id`, `name`, `emoji` (el emoji elegido como icono del hábito en TickTick, cadena vacía si no tiene — **no** viene de `name`: TickTick lo guarda en `iconRes` como `"txt_<emoji>"` cuando el usuario elige un emoji como icono; los iconos predefinidos usan otros valores como `"habit_daily_check_in"`), `is_done_today`, `is_locked` (por defecto `False`: la tecla siempre admite pulsaciones), `display_label` (por defecto el nombre del hábito; el emoji se pinta aparte como icono, ver `render_primitives.text_tile`), `build_checkin_payload(stamp, current_value=0.0)` abstracto.
  - `boolean.py`: `BooleanHabit` — checkin siempre `{value: 1.0, goal: 1.0}`; ignora `current_value` y no sobrescribe `is_locked`/`display_label` (pulsar un hábito ya hecho vuelve a enviar el mismo checkin, idempotente).
  - `real.py`: `RealHabit` (hábitos cuantificables, p.ej. "Drink Water" con `goal`/`step`/`unit`) — cada pulsación suma `step` al progreso acumulado de hoy sin superar `goal` (`build_checkin_payload` calcula `min(current_value + step, goal)`); `is_locked` bloquea la tecla en cuanto se alcanza el objetivo (no se puede superar); `display_label` sobrescribe el de la base para mostrar *solo* el progreso (p.ej. `"3/8 Cups"`), sin el nombre — el emoji (si lo tiene) ya identifica el hábito como icono aparte.
  - `registry.py`: `build_habit(data)` — enruta `"Boolean"` a `BooleanHabit` y `"Real"` a `RealHabit`; tipos desconocidos caen en `BooleanHabit`.
- **`error_codes.py`** — `CODES`: diccionario de códigos cortos (`T401`, `TNET`, `TERR`, `KFUL`) mostrados en tecla o en logs.
- **`health.py`** — `classify(exc)` decide si un fallo del ciclo se muestra en tecla (`"key"`, errores de la API de TickTick) o solo se loguea a fichero (`"file"`, fallos del propio Stream Deck); `log_failure` escribe a `checkin_failures.log`, `log_device_error` a `device_errors.log`.
- **`render_primitives.py`** — helpers Pillow de bajo nivel: `solid_tile` (color plano) y `text_tile` (texto envuelto y centrado sobre un color, más un `emoji` opcional pintado como icono a color en la mitad superior de la tecla, dejando el texto centrado en el espacio restante — o la tecla entera si `text` es cadena vacía), ambos vía `PILHelper.create_image`/`to_native_format`. El emoji se renderiza con `_emoji_font()` (`ImageFont.truetype` sobre `/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos del paquete ya que es una fuente de mapa de bits CBDT/CBLC con un único tamaño de glifo grabado) y `_emoji_glyph()` (dibuja con `embedded_color=True` sobre un lienzo RGBA transparente y lo reescala al tamaño de icono deseado); si la fuente no está instalada (`ImageFont.truetype` lanza `OSError`) `_emoji_font()` devuelve `None` y `text_tile` simplemente no pinta icono, sin fallar.
- **`deck_renderer.py`** — helpers de alto nivel sobre una tecla o el deck completo: `render_habit` (pinta usando `habit.display_label(current_value)` como texto y `habit.emoji` como icono, no `habit.name` directamente), `render_checkin_error`, `render_reserved`, `render_empty`, `render_all` (repinta las 15 teclas según mapeo/hábitos/checkins/progreso — recibe también `values_by_id` para los hábitos cuantificables), `render_error_all` (pinta con el código de error todas las teclas actualmente mapeadas, para no dejar info obsoleta en pantalla cuando falla la lectura de hábitos o checkins).
- **`special_keys.py`** — `render_reserved_keys`: pinta las 3 teclas reservadas. `handle_key_press(key, refresh_event)`: la tecla `KEY_REFRESH` (0) activa `refresh_event` para forzar un refresco inmediato; las teclas 5 y 10 siguen siendo placeholder sin acción.
- **`deck_session.py`** — `DeckSession`: apertura/cierre/reconexión del dispositivo y brillo (`BRIGHTNESS = 60`). No sabe nada de hábitos ni de la API de TickTick. `reconnect()` se usa solo tras un fallo de dispositivo en marcha, nunca en el arranque inicial (eso lo hace `open()`, con reintentos).
- **`orchestrator.py`** — bucle principal (`main` → `refresh_cycle`, cada `REFRESH_SECONDS` o antes si se activa `refresh_event`): fetch hábitos → `update_mapping` → fetch progreso de hoy vía `get_checkin_progress_for` (`stamp` = `YYYYMMDD` local; de ahí se derivan `done_ids` y `values_by_id`) → `render_all` → **re-registra** `deck.set_key_callback(...)` con un closure fresco sobre el mapeo actual. Esto significa que el mapeo tecla→hábito usado al pulsar solo es tan reciente como el último ciclo de refresco, no en vivo. El estado pasado al callback usa wrappers dict de una entrada (`habits_ref = {"value": ...}`, `done_ids_ref`, `values_ref`) para que el closure observe actualizaciones de ciclos posteriores — aunque el callback igualmente se recrea cada ciclo. El bucle principal espera con `refresh_event.wait(timeout=REFRESH_SECONDS)` en vez de `time.sleep`: pulsar `KEY_REFRESH` (tecla 0, vía `special_keys.handle_key_press`) activa ese mismo `Event` desde el hilo de callbacks del Stream Deck, despertando el bucle principal para que ejecute `refresh_cycle` de inmediato — la API sigue siendo la única fuente de verdad, solo cambia cuándo se consulta. `Event.set()` es idempotente, así que pulsaciones repetidas mientras ya hay un refresco en curso no encolan refrescos extra. `make_key_callback` evita checkins duplicados por hábito con un `pending_requests` set global + `state_lock` (una doble pulsación mientras hay una petición en vuelo se ignora), y antes de construir el payload comprueba `habit.is_locked(done_ids)`: si el hábito ya alcanzó su objetivo hoy (los cuantificables no admiten superarlo) la pulsación se ignora sin llamar a la API. En caso contrario construye el payload con `habit.build_checkin_payload(stamp, current_value)` (`current_value` viene de `values_ref`, 0.0 si aún no hay checkin hoy). Al éxito, `done_now` se calcula comparando `payload["value"] >= payload["goal"]` (no se asume "hecho" solo por haber tenido éxito, ya que un hábito cuantificable puede seguir en progreso parcial); la tecla se repinta de inmediato con `render_habit(..., done=done_now, current_value=new_value)` (actualización optimista local, sin refetch) y `values_ref`/`done_ids_ref` se actualizan para que la siguiente pulsación en el mismo ciclo parta del valor correcto. Al fallo se pinta en rojo con el código de error y el fallo se añade a `checkin_failures.log`. Cualquier excepción que no sea de la API de TickTick (ya gestionadas dentro de `refresh_cycle`) se trata como error de dispositivo: nunca se muestra en tecla, y dispara `session.reconnect()`.

## Convenciones

- Los mensajes de log de cara al usuario y los comentarios de código están en español; mantén los nuevos consistentes con eso.
- Todos los `print` usan `flush=True` porque esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).
- El estilo de código (ruff, mypy, type hints, docstrings) se detalla en [Estilo de código y herramientas](#estilo-de-código-y-herramientas); mantén los módulos nuevos consistentes con ese estándar.
- **Mantén este fichero al día**: si al hacer un cambio descubres que algo aquí descrito no coincide con el comportamiento real del código (una descripción desactualizada, un comando que ya no funciona como se documenta, un color/valor que cambió, etc.), corrígelo en el mismo turno en vez de dejarlo pasar. Este documento solo es útil si las próximas sesiones pueden confiar en él.
