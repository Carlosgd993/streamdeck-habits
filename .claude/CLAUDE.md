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

Para pintar el emoji del nombre de un hábito como icono a color en la tecla hace falta además la fuente `fonts-noto-color-emoji` instalada a nivel de sistema en la Pi (paquete Debian, no es un paquete de Python): `sudo apt install fonts-noto-color-emoji` (requiere contraseña de sudo interactiva — no se puede instalar por SSH no interactivo desde esta máquina). Si no está instalada, `deck/primitives.py::_emoji_font()` devuelve `None` y la tecla simplemente no pinta el icono (se degrada, no falla).

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
  'd=$(mktemp -d); tar xf - -C "$d"; cd "$d"; /opt/streamdeck-habits/venv/bin/python -c "import orchestrator, provider.base, provider.ticktick, core.key_map, core.health, core.error_codes, core.emoji, deck.session, deck.primitives, deck.renderer, deck.keys; print(\"IMPORTS_OK\")"; rc=$?; cd /; rm -rf "$d"; exit $rc'
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

El código está organizado en **tres capas por carpetas**, con una regla de dependencia clara: `provider/` no sabe nada del Stream Deck; `deck/` no sabe nada del proveedor de datos; `core/` y `orchestrator.py` orquestan ambos hablando solo con abstracciones. `orchestrator.py` y `config.py` viven en la raíz (el primero es el `ExecStart` de systemd; ambos son compartidos por las tres capas).

El eje del diseño es un **puerto/adaptador (hexagonal)**: `provider/base.py` define el puerto abstracto y TickTick es solo un adaptador detrás de él. Sustituir de API = escribir otro adaptador que implemente `HabitProvider` y cambiar **una línea** en `orchestrator.main()` (`TickTickProvider()`); ni `core/`, ni `deck/`, ni el resto del orquestador cambian.

### Raíz (compartido)

- **`config.py`** — constantes compartidas y agnósticas del proveedor: rutas (`BASE_DIR`, `ENV_FILE`, `MAP_FILE`, logs), `RESERVED_KEYS = {0, 5, 10}` / `AVAILABLE_KEYS` (15 teclas, 3 reservadas), `KEY_REFRESH = 0` (tecla de refresco manual), `REFRESH_SECONDS` (900s). Los colores/fuentes **ya no están aquí** (viven en `deck/style.py`).
- **`orchestrator.py`** — punto de entrada; depende solo del puerto (`provider.base`) y de `core/`/`deck/`. Ver [El bucle principal](#el-bucle-principal-orchestratorpy) abajo.

### `provider/` — la API, aislada tras un puerto

- **`provider/base.py`** — el **puerto**. Contiene TODO lo que el resto del proyecto necesita saber de "la API", sin acoplarse a ningún backend:
  - **`HabitProvider`** (ABC): interfaz con `get_habits() -> list[Habit]`, `get_progress(habit_ids, day) -> dict[str, Progress]` y `checkin(habit, day, value) -> None`. `day` es un `datetime.date` (no el `stamp` `YYYYMMDD`, que es formato TickTick).
  - **Excepciones agnósticas**: `ProviderError` (base) → `ProviderAuthError`, `ProviderNetworkError`, `ProviderDataError`.
  - **`Progress`** (dataclass): `value`/`goal` del checkin de un día (el `goal` es el vigente cuando se registró, no necesariamente el actual); el orquestador deriva de ahí tanto `done_ids` (`value >= goal`) como el progreso acumulado.
  - **Modelo de dominio `Habit`** (agnóstico, construido desde campos ya parseados — **no** desde JSON crudo): `id`, `name`, `emoji`, `order` (pista de orden para asignar teclas), `is_done_today`, `is_locked` (por defecto `False`), `display_label` (por defecto el nombre), `goal` (propiedad, por defecto `1.0`) y `next_value(current_value)` abstracto (el valor TOTAL tras una pulsación; el dominio lo decide, el adaptador lo traduce a su llamada). `BooleanHabit`: `next_value` → `1.0`. `RealHabit` (cuantificables, con `goal`/`step`/`unit`): `next_value` → `min(current+step, goal)`, `is_locked` bloquea al alcanzar el objetivo, `display_label` muestra *solo* el progreso (p.ej. `"3/8 Cups"`).
- **`provider/ticktick.py`** — el **adaptador** de TickTick; concentra todo lo específico del backend (antes repartido entre `auth.py`, `ticktick_client.py` y `habits/`):
  - Carga del token del `.env` (`TICKTICK_ACCESS_TOKEN`); `TickTickProvider.__init__` lanza `ProviderAuthError` si falta.
  - `TickTickProvider(HabitProvider)`: llamadas `requests` contra `api.ticktick.com/open/v1/habit*`, traduciendo `RequestException`/401/no-200/JSON-inválido a las excepciones `Provider*`. `get_progress` maneja el `to` exclusivo de TickTick (pide `day+1`). El checkin **no es incremental**: cada `POST .../checkin` hace upsert del `value` total de ese `stamp`, por eso `checkin` recibe el total ya calculado (vía `habit.next_value`) y arma `{stamp, value, goal: habit.goal}`.
  - `build_habit(data)`: mapea el JSON crudo de TickTick al dominio — parsea `iconRes` (`"txt_<emoji>"` → emoji del icono, vía `core.emoji.extract_emoji`; los iconos predefinidos como `"habit_daily_check_in"` dan emoji vacío), enruta `type == "Real"` a `RealHabit` y el resto (incluido `"Boolean"` y desconocidos) a `BooleanHabit`, y toma `order` de `sortOrder`.

### `core/` — dominio/orquestación agnósticos

- **`core/key_map.py`** — `load_map`/`save_map`/`update_mapping`: persiste el mapeo hábito→tecla en `habit_key_map.json`. Recibe `list[Habit]` (no JSON). Los hábitos nuevos reclaman la tecla libre más baja en orden `(habit.order, habit.id)` (nunca se reasignan); los que desaparecen liberan su tecla. Sin teclas libres → se loguea omitido (código `KFUL`). **Importante**: solo debe llamarse tras un `get_habits()` exitoso — nunca tras un fallo, o se liberarían teclas de hábitos que siguen existiendo.
- **`core/error_codes.py`** — `CODES`: códigos cortos agnósticos mostrados en tecla o logs (`AUTH`, `NET`, `API`, `KFUL`).
- **`core/health.py`** — `classify(exc)` decide si un fallo se muestra en tecla (`"key"`, excepciones `Provider*` → `AUTH`/`NET`/`API`) o solo se loguea a fichero (`"file"`, fallos del propio Stream Deck); `log_failure` → `checkin_failures.log`, `log_device_error` → `device_errors.log`.
- **`core/emoji.py`** — `extract_emoji(text)`: separa el primer emoji (o secuencia emoji, incluyendo variation selector y ZWJ) de una cadena → `(emoji, resto)`. Sin dependencias de Pillow/StreamDeck ni de ningún proveedor, para poder usarse desde cualquier adaptador.

### `deck/` — la Stream Deck (hardware + pintado)

- **`deck/style.py`** — colores (`COLOR_*`) y tamaños de fuente (`FONT_SIZE_*`) de las teclas. Es la capa de pintado, no sabe de hábitos ni del proveedor.
- **`deck/primitives.py`** — helpers Pillow de bajo nivel: `solid_tile` (color plano) y `text_tile` (texto envuelto/centrado sobre un color, más un `emoji` opcional pintado como icono a color en la mitad superior — o la tecla entera si `text` es vacío), vía `PILHelper`. El emoji usa `_emoji_font()` (`ImageFont.truetype` sobre `/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos porque es una fuente CBDT/CBLC de mapa de bits) y `_emoji_glyph()` (`embedded_color=True`, reescalado); si la fuente no está instalada, `_emoji_font()` devuelve `None` y no se pinta icono, sin fallar.
- **`deck/renderer.py`** — helpers de alto nivel sobre una tecla o el deck completo: `render_habit` (usa `habit.display_label(current_value)` como texto y `habit.emoji` como icono), `render_checkin_error`, `render_reserved`, `render_empty`, `render_all` (repinta las 15 teclas según mapeo/hábitos/checkins/progreso, recibe `values_by_id`), `render_error_all` (pinta el código de error en todas las teclas mapeadas cuando falla una lectura, para no dejar info obsoleta).
- **`deck/keys.py`** — `render_reserved_keys`: pinta las 3 teclas reservadas. `handle_key_press(key, refresh_event)`: `KEY_REFRESH` (0) activa `refresh_event` para forzar un refresco; 5 y 10 son placeholder sin acción.
- **`deck/session.py`** — `DeckSession`: apertura/cierre/reconexión del dispositivo y brillo (`BRIGHTNESS = 60`). No sabe nada de hábitos ni del proveedor. `reconnect()` se usa solo tras un fallo de dispositivo en marcha, nunca en el arranque inicial (eso lo hace `open()`, con reintentos).

### El bucle principal (`orchestrator.py`)

Bucle principal (`main` → `refresh_cycle`, cada `REFRESH_SECONDS` o antes si se activa `refresh_event`): `provider.get_habits()` → `key_map.update_mapping` → `provider.get_progress(ids, day)` (`day` = `date.today()` local; de ahí se derivan `done_ids` y `values_by_id` a partir de los `Progress`) → `renderer.render_all` → **re-registra** `deck.set_key_callback(...)` con un closure fresco sobre el mapeo actual. El mapeo tecla→hábito usado al pulsar solo es tan reciente como el último ciclo. El estado pasado al callback usa wrappers dict de una entrada (`habits_ref = {"value": ...}`, `done_ids_ref`, `values_ref`) para que el closure observe actualizaciones de ciclos posteriores. El bucle espera con `refresh_event.wait(timeout=REFRESH_SECONDS)`: pulsar `KEY_REFRESH` (vía `deck.keys.handle_key_press`) activa ese `Event` desde el hilo de callbacks, despertando el bucle para un `refresh_cycle` inmediato — el proveedor sigue siendo la única fuente de verdad, solo cambia cuándo se consulta. `Event.set()` es idempotente. `make_key_callback` evita checkins duplicados por hábito con `pending_requests` + `state_lock`; comprueba `habit.is_locked(done_ids)` (si ya alcanzó el objetivo, ignora la pulsación sin llamar al proveedor) y si `habit is None` (caso defensivo entre ciclos) también la ignora. Si no, calcula `new_value = habit.next_value(current_value)` (`current_value` de `values_ref`, 0.0 si no hay checkin hoy) y llama `provider.checkin(habit, day, new_value)`. Al éxito, `done_now = new_value >= habit.goal` (un cuantificable puede seguir en progreso parcial); la tecla se repinta de inmediato (`render_habit(..., done=done_now, current_value=new_value)`, optimista, sin refetch) y `values_ref`/`done_ids_ref` se actualizan para la siguiente pulsación del mismo ciclo. Al fallo (`ProviderError`) se pinta en rojo con el código y se añade a `checkin_failures.log`. Cualquier excepción ajena al proveedor (ya gestionadas en `refresh_cycle`) se trata como error de dispositivo: nunca en tecla, y dispara `session.reconnect()`.

## Convenciones

- Los mensajes de log de cara al usuario y los comentarios de código están en español; mantén los nuevos consistentes con eso.
- Todos los `print` usan `flush=True` porque esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).
- El estilo de código (ruff, mypy, type hints, docstrings) se detalla en [Estilo de código y herramientas](#estilo-de-código-y-herramientas); mantén los módulos nuevos consistentes con ese estándar.
- **Mantén este fichero al día**: si al hacer un cambio descubres que algo aquí descrito no coincide con el comportamiento real del código (una descripción desactualizada, un comando que ya no funciona como se documenta, un color/valor que cambió, etc.), corrígelo en el mismo turno en vez de dejarlo pasar. Este documento solo es útil si las próximas sesiones pueden confiar en él.
