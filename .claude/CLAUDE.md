# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un demonio Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos sobre una base de datos propia en Supabase. Corre en una Raspberry Pi 3, sondea la base de datos vía PostgREST, ilumina cada tecla en blanco (pendiente) o gris oscuro (hecho hoy), y registra un checkin en la base de datos al pulsar una tecla.

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
  'd=$(mktemp -d); tar xf - -C "$d"; cd "$d"; /opt/streamdeck-habits/venv/bin/python -c "import orchestrator, provider.base, provider.supabase, core.key_map, core.health, core.error_codes, core.emoji, deck.session, deck.primitives, deck.renderer, deck.keys; print(\"IMPORTS_OK\")"; rc=$?; cd /; rm -rf "$d"; exit $rc'
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
- `.env` — debe definir `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` (cargados vía `python-dotenv` en `SupabaseProvider.__init__`)
- `habit_key_map.json` — mapeo persistido `habit_id -> key_index` (se crea/actualiza solo)
- `checkin_failures.log` — log JSON-lines de checkins fallidos hacia Supabase (errores de API, tecla en rojo)
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

El eje del diseño es un **puerto/adaptador (hexagonal)**: `provider/base.py` define el puerto abstracto y Supabase es solo un adaptador detrás de él. Sustituir de API = escribir otro adaptador que implemente `HabitProvider` y cambiar **una línea** en `orchestrator.main()` (`SupabaseProvider()`); ni `core/`, ni `deck/`, ni el resto del orquestador cambian.

### Raíz (compartido)

- **`config.py`** — constantes compartidas y agnósticas del proveedor: rutas (`BASE_DIR`, `ENV_FILE`, `MAP_FILE`, logs), `RESERVED_KEYS = {0, 5, 10}` / `AVAILABLE_KEYS` (15 teclas, 3 reservadas), `KEY_REFRESH = 0` (tecla de refresco manual), `REFRESH_SECONDS` (900s). Los colores/fuentes **ya no están aquí** (viven en `deck/style.py`).
- **`orchestrator.py`** — punto de entrada; depende solo del puerto (`provider.base`) y de `core/`/`deck/`. Ver [El bucle principal](#el-bucle-principal-orchestratorpy) abajo.

### `provider/` — la API, aislada tras un puerto

- **`provider/base.py`** — el **puerto**. Contiene TODO lo que el resto del proyecto necesita saber de "la API", sin acoplarse a ningún backend:
  - **`HabitProvider`** (ABC): interfaz con `get_habits() -> list[Habit]` (ya trae el progreso de hoy) y `step(habit) -> float` (avanza un paso, devuelve el nuevo total). La lógica de "qué día es hoy" y "cuál es el siguiente valor" vive en la base de datos (contrato de `habits-core`); el puerto y sus adaptadores son un renderizador tonto sobre lo que la base ya calculó.
  - **Excepciones agnósticas**: `ProviderError` (base) → `ProviderAuthError`, `ProviderNetworkError`, `ProviderDataError`.
  - **Modelo de dominio `Habit`** (agnóstico, construido desde campos ya parseados — **no** desde JSON crudo): `id`, `name`, `emoji`, `order` (pista de orden para asignar teclas), `current_value` (progreso de hoy, poblado por el provider; puede superar `goal` — `10/8` es válido), `goal` (propiedad, por defecto `1.0`), `is_done` (propiedad, `current_value >= goal`, solo para decidir el color de la tecla — no bloquea nada) y `display_label()` abstracto (texto a mostrar, leyendo `current_value` del propio hábito). `BooleanHabit.display_label` → el nombre. `RealHabit` (cuantificables, con `goal`/`step`/`unit`): `display_label` → solo el progreso (p.ej. `"3/8 Cups"`, o `"10/8 Cups"` por encima del objetivo, sin decimales feos en valores enteros).
- **`provider/supabase.py`** — el **adaptador** de Supabase; concentra todo lo específico del backend:
  - Carga de `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` del `.env`; `SupabaseProvider.__init__` lanza `ProviderAuthError` si falta alguna. La base de PostgREST es `<url>/rest/v1`; cada petición manda las cabeceras `apikey` + `Authorization: Bearer <key>`.
  - `SupabaseProvider(HabitProvider)`: llamadas `requests` (PostgREST) contra el contrato público — la vista `v_today_habits` y la función `rpc/habit_step` — traduciendo `RequestException`/401-403/no-2xx/JSON-inválido a las excepciones `Provider*`. Las tablas (`habits`, `habit_checkins`, …) están cerradas con RLS y no son accesibles con la clave publishable; este adaptador nunca las menciona.
  - `get_habits`: una sola petición a `v_today_habits` (la vista ya filtra activos y trae el progreso de hoy), pidiendo `order=sort_order` porque la vista no ordena por sí sola.
  - `step`: un `POST /rpc/habit_step` con `{"p_habit_id": habit.id}`, atómico en la base (Boolean → salta a `goal`; Real → suma `step` sin tope). Devuelve el nuevo total como `float`.
  - Un 401/403 puede significar clave inválida **o** que falta un `GRANT` del contrato; el mensaje de error lo insinúa.
  - `build_habit(data)`: mapea la fila de la vista al dominio — parsea `icon_res` (`"txt_<emoji>"` → emoji del icono, vía `core.emoji.extract_emoji`; los iconos predefinidos como `"habit_water"` dan emoji vacío), enruta `type == "Real"` a `RealHabit` y el resto (incluido `"Boolean"` y desconocidos) a `BooleanHabit`, toma `order` de `sort_order` y `current_value` de la columna homónima.
  - El contrato (qué vistas/funciones existen, qué garantizan) está documentado en `habits-core/docs/contrato.md`, fuera de este repo — ver `.claude/estructura-bd.md` para la regla de por qué. Hay peticiones PostgREST de ejemplo/documentación en `.claude/supabase.http`.

### `core/` — dominio/orquestación agnósticos

- **`core/key_map.py`** — `load_map`/`save_map`/`update_mapping`: persiste el mapeo hábito→tecla en `habit_key_map.json`. Recibe `list[Habit]` (no JSON). Los hábitos nuevos reclaman la tecla libre más baja en orden `(habit.order, habit.id)` (nunca se reasignan); los que desaparecen liberan su tecla. Sin teclas libres → se loguea omitido (código `KFUL`). **Importante**: solo debe llamarse tras un `get_habits()` exitoso — nunca tras un fallo, o se liberarían teclas de hábitos que siguen existiendo.
- **`core/error_codes.py`** — `CODES`: códigos cortos agnósticos mostrados en tecla o logs (`AUTH`, `NET`, `API`, `KFUL`).
- **`core/health.py`** — `classify(exc)` decide si un fallo se muestra en tecla (`"key"`, excepciones `Provider*` → `AUTH`/`NET`/`API`) o solo se loguea a fichero (`"file"`, fallos del propio Stream Deck); `log_failure` → `checkin_failures.log`, `log_device_error` → `device_errors.log`.
- **`core/emoji.py`** — `extract_emoji(text)`: separa el primer emoji (o secuencia emoji, incluyendo variation selector y ZWJ) de una cadena → `(emoji, resto)`. Sin dependencias de Pillow/StreamDeck ni de ningún proveedor, para poder usarse desde cualquier adaptador.

### `deck/` — la Stream Deck (hardware + pintado)

- **`deck/style.py`** — colores (`COLOR_*`) y tamaños de fuente (`FONT_SIZE_*`) de las teclas. Es la capa de pintado, no sabe de hábitos ni del proveedor.
- **`deck/primitives.py`** — helpers Pillow de bajo nivel: `solid_tile` (color plano) y `text_tile` (texto envuelto/centrado sobre un color, más un `emoji` opcional pintado como icono a color en la mitad superior — o la tecla entera si `text` es vacío), vía `PILHelper`. El emoji usa `_emoji_font()` (`ImageFont.truetype` sobre `/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos porque es una fuente CBDT/CBLC de mapa de bits) y `_emoji_glyph()` (`embedded_color=True`, reescalado); si la fuente no está instalada, `_emoji_font()` devuelve `None` y no se pinta icono, sin fallar.
- **`deck/renderer.py`** — helpers de alto nivel sobre una tecla o el deck completo: `render_habit` (usa `habit.display_label()` como texto, `habit.emoji` como icono y `habit.is_done` para elegir blanco/pendiente o gris/hecho — sin bloquear nada, solo color), `render_checkin_error`, `render_reserved`, `render_empty`, `render_all` (repinta las 15 teclas según mapeo y hábitos, cada uno con su progreso ya incluido), `render_error_all` (pinta el código de error en todas las teclas mapeadas cuando falla una lectura, para no dejar info obsoleta).
- **`deck/keys.py`** — `render_reserved_keys`: pinta las 3 teclas reservadas. `handle_key_press(key, refresh_event)`: `KEY_REFRESH` (0) activa `refresh_event` para forzar un refresco; 5 y 10 son placeholder sin acción.
- **`deck/session.py`** — `DeckSession`: apertura/cierre/reconexión del dispositivo y brillo (`BRIGHTNESS = 60`). No sabe nada de hábitos ni del proveedor. `reconnect()` se usa solo tras un fallo de dispositivo en marcha, nunca en el arranque inicial (eso lo hace `open()`, con reintentos).

### El bucle principal (`orchestrator.py`)

Bucle principal (`main` → `refresh_cycle`, cada `REFRESH_SECONDS` o antes si se activa `refresh_event`): `provider.get_habits()` (una sola petición; ya trae el progreso de hoy en `habit.current_value`, no hay fecha que decidir en el cliente) → `key_map.update_mapping` → `renderer.render_all` → **re-registra** `deck.set_key_callback(...)` con un closure fresco sobre el mapeo actual. El mapeo tecla→hábito usado al pulsar solo es tan reciente como el último ciclo. El estado pasado al callback usa un wrapper dict de una entrada (`habits_ref = {"value": {id: Habit}}`) para que el closure observe actualizaciones de ciclos posteriores. El bucle espera con `refresh_event.wait(timeout=REFRESH_SECONDS)`: pulsar `KEY_REFRESH` (vía `deck.keys.handle_key_press`) activa ese `Event` desde el hilo de callbacks, despertando el bucle para un `refresh_cycle` inmediato — el proveedor sigue siendo la única fuente de verdad, solo cambia cuándo se consulta. `Event.set()` es idempotente. `make_key_callback` evita pasos duplicados por hábito con `pending_requests` + `state_lock`; si `habit is None` (caso defensivo entre ciclos) ignora la pulsación. Si no, llama `provider.step(habit)` — sin comprobar si ya alcanzó el objetivo: la tecla nunca se bloquea, es la base quien decide el nuevo valor (`habit_step`, atómico). Al éxito, `habit.current_value = new_value` (mutación directa del objeto compartido con `habits_ref`) y la tecla se repinta de inmediato (`render_habit`, que lee `habit.is_done`/`display_label()` del propio hábito ya actualizado, optimista, sin refetch). Al fallo (`ProviderError`) se pinta en rojo con el código y se añade a `checkin_failures.log`. Cualquier excepción ajena al proveedor (ya gestionadas en `refresh_cycle`) se trata como error de dispositivo: nunca en tecla, y dispara `session.reconnect()`.

## Convenciones

- Los mensajes de log de cara al usuario y los comentarios de código están en español; mantén los nuevos consistentes con eso.
- Todos los `print` usan `flush=True` porque esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).
- El estilo de código (ruff, mypy, type hints, docstrings) se detalla en [Estilo de código y herramientas](#estilo-de-código-y-herramientas); mantén los módulos nuevos consistentes con ese estándar.
- **Mantén este fichero al día**: si al hacer un cambio descubres que algo aquí descrito no coincide con el comportamiento real del código (una descripción desactualizada, un comando que ya no funciona como se documenta, un color/valor que cambió, etc.), corrígelo en el mismo turno en vez de dejarlo pasar. Este documento solo es útil si las próximas sesiones pueden confiar en él.
