# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un daemon Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos. Corre 24/7 como servicio systemd en una Raspberry Pi 3, sondea la base de datos vía PostgREST cada 15 minutos, pinta cada tecla en blanco (pendiente) o gris oscuro (hecho hoy), y registra un checkin al pulsarla.

La base de datos vive en el repo hermano `../habits-core` (repositorio Git independiente). Este daemon es deliberadamente **tonto**: no calcula qué día es hoy, ni el siguiente valor de un hábito, ni si algo está bloqueado. Todo eso lo decide la base. Ver [../CLAUDE.md](../CLAUDE.md) para el contexto que atraviesa ambos repos.

**No hay tests automatizados ni lockfile de dependencias, y nada de esto se ejecuta en la máquina de desarrollo**: no tiene Python usable (solo el stub de la Microsoft Store) ni el hardware. Todo se verifica en la Pi por SSH — ver [Operar la Raspberry Pi](#operar-la-raspberry-pi).

## Mapa del código

Son ~1.100 líneas en total: leer un módulo entero es barato, la duda suele ser cuál.

```
orchestrator.py   170  Punto de entrada (ExecStart de systemd). Bucle y callbacks de tecla.
config.py          22  Rutas, teclas reservadas, intervalo de refresco.

provider/              LA API, aislada tras un puerto. No sabe nada del deck.
  base.py         181  El puerto: HabitProvider, Habit/BooleanHabit/RealHabit, excepciones.
  supabase.py     173  El adaptador: PostgREST, build_habit(). Único sitio con detalles de Supabase.

deck/                  EL HARDWARE. No sabe nada del proveedor de datos.
  session.py       76  Abrir/cerrar/reconectar el dispositivo, brillo.
  renderer.py     103  Pintado de alto nivel: render_habit, render_all, render_error_all…
  primitives.py   128  Pillow de bajo nivel: solid_tile, text_tile, fuente de emoji.
  keys.py          53  Teclas reservadas y qué hace cada una.
  style.py         19  Colores y tamaños de fuente. Nada más.

core/                  DOMINIO. Agnóstico de ambos lados.
  key_map.py       73  Asignación persistente hábito → tecla.
  health.py        47  Clasificar un fallo: ¿tecla en rojo o solo log?
  error_codes.py   14  AUTH / NET / API / KFUL.
  emoji.py         40  extract_emoji(): separa el primer emoji de una cadena.

deploy/deploy.sh       Despliegue en la Pi (normal y --test).
deploy/*.service       Unit de systemd. No se instala sola.
scripts/               Dos smoke tests de hardware. Requieren un deck conectado.
```

| Si vas a cambiar… | Toca | Ojo con |
|---|---|---|
| El aspecto de una tecla (color, tamaño) | `deck/style.py` | Nada más; el resto solo consume esas constantes |
| Qué texto o icono muestra un hábito | `provider/base.py` → `display_label()` | Es del dominio, no del pintado |
| Qué hace una tecla reservada | `config.py` + `deck/keys.py` | Ver [Teclas reservadas](#teclas-reservadas) |
| Cómo se habla con la base | `provider/supabase.py` **y solo ahí** | Si tocas otro sitio, has roto el aislamiento |
| Qué campos trae un hábito | `provider/supabase.py::build_habit` + `provider/base.py` | La columna debe existir ya en la vista de `habits-core` |
| Cuándo se refresca o qué pasa al pulsar | `orchestrator.py` | Ver [El bucle principal](#el-bucle-principal) |
| Qué se muestra al fallar algo | `core/health.py` + `core/error_codes.py` | Los fallos de dispositivo nunca van a tecla |
| Añadir una capacidad nueva (tareas, undo…) | **Primero** el contrato en `../habits-core` | El daemon no inventa lógica |

## Arquitectura

Tres capas por carpeta, con una regla de dependencia estricta: **`provider/` no sabe nada del Stream Deck; `deck/` no sabe nada del proveedor de datos; `core/` y `orchestrator.py` orquestan ambos hablando solo con abstracciones.** `orchestrator.py` y `config.py` viven en la raíz porque los comparten las tres capas (el primero es además el `ExecStart` de systemd).

El eje del diseño es un **puerto/adaptador (hexagonal)**: `provider/base.py` define el puerto abstracto y Supabase es solo un adaptador detrás de él. Cambiar de backend = escribir otro `HabitProvider` y tocar **una línea** en `orchestrator.main()` (`SupabaseProvider()`); ni `core/`, ni `deck/`, ni el resto del orquestador cambian.

### `provider/` — la API tras un puerto

- **`base.py`** — el **puerto**. Contiene todo lo que el resto del proyecto necesita saber de "la API", sin acoplarse a ningún backend:
  - **`HabitProvider`** (ABC): `get_habits() -> list[Habit]` (ya trae el progreso de hoy) y `step(habit) -> float` (avanza un paso, devuelve el nuevo total).
  - **Excepciones agnósticas**: `ProviderError` → `ProviderAuthError`, `ProviderNetworkError`, `ProviderDataError`.
  - **`Habit`** (ABC), construido desde campos ya parseados, **no** desde JSON crudo: `id`, `name`, `emoji`, `order` (pista para asignar teclas), `current_value` (progreso de hoy, **puede superar `goal`** — 10/8 es válido); propiedades `goal` (default `1.0`) e `is_done` (`current_value >= goal`, que **solo decide el color de la tecla, no bloquea nada**); `display_label()` abstracto. `BooleanHabit` → muestra el nombre. `RealHabit` (cuantificables, con `goal`/`step`/`unit`) → muestra solo el progreso (`"3/8 Cups"`, o `"10/8 Cups"` por encima del objetivo, sin decimales feos en enteros).
- **`supabase.py`** — el **adaptador**; concentra todo lo específico del backend:
  - Lee `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` del `.env`; `__init__` lanza `ProviderAuthError` si falta alguna. Base PostgREST `<url>/rest/v1`, cabeceras `apikey` + `Authorization: Bearer`.
  - `get_habits`: **una sola petición** a `v_today_habits` (la vista ya filtra activos y trae el progreso), con `order=sort_order` porque la vista no ordena por sí sola.
  - `step`: `POST /rpc/habit_step` con `{"p_habit_id": habit.id}`. Atómico en la base (Boolean → salta a `goal`; Real → suma `step` sin tope). Devuelve el nuevo total.
  - Traduce `RequestException` / 401-403 / no-2xx / JSON inválido a las excepciones `Provider*`. Un 401/403 puede ser clave inválida **o** un `GRANT` que falta en el contrato; el mensaje lo insinúa.
  - `build_habit(data)`: parsea `icon_res` (`"txt_<emoji>"` → emoji vía `core.emoji.extract_emoji`; los predefinidos tipo `"habit_water"` dan emoji vacío), enruta `type == "Real"` a `RealHabit` y todo lo demás (incluido `"Boolean"` y valores desconocidos) a `BooleanHabit`, toma `order` de `sort_order`.
  - Las tablas (`habits`, `habit_checkins`, …) están cerradas con RLS y no son accesibles con la clave publishable: este adaptador nunca las menciona. El contrato está en `../habits-core/docs/contrato.md`; hay un resumen orientado a cliente en `.claude/tables-doc.md`, el porqué de la regla en `.claude/estructura-bd.md` y peticiones de ejemplo en `.claude/supabase.http`.

### `deck/` — hardware y pintado

- **`session.py`** — `DeckSession`: apertura (30 reintentos cada 2s; `sys.exit(1)` si no aparece), cierre, `reconnect()` y brillo (`BRIGHTNESS = 60`). `reconnect()` solo se usa tras un fallo en marcha, nunca en el arranque inicial.
- **`renderer.py`** — alto nivel, sobre una tecla o el deck completo: `render_habit` (usa `display_label()`, `emoji` e `is_done` para elegir blanco/pendiente o gris/hecho — solo color, sin bloquear nada), `render_checkin_error`, `render_reserved`, `render_shutdown` (fondo rojo de aviso, "APAGAR" e icono 🔴, para distinguirla de una reservada normal), `render_empty`, `render_all` (repinta las 15), `render_error_all` (pinta el código de error en todas las teclas mapeadas cuando falla una lectura, para no dejar información obsoleta en pantalla).
- **`primitives.py`** — Pillow de bajo nivel vía `PILHelper`: `solid_tile` (color plano) y `text_tile` (texto envuelto/centrado más un `emoji` opcional como icono a color en la mitad superior, o la tecla entera si `text` es vacío). El emoji usa `_emoji_font()` (`NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos porque es una fuente CBDT/CBLC de mapa de bits) y `_emoji_glyph()` (`embedded_color=True`, reescalado).
- **`keys.py`** — ver [Teclas reservadas](#teclas-reservadas).
- **`style.py`** — todos los colores (`COLOR_*`) y tamaños de fuente (`FONT_SIZE_*`). Es la capa de pintado: no sabe de hábitos ni del proveedor.

### `core/` — dominio agnóstico

- **`key_map.py`** — persiste el mapeo hábito→tecla en `habit_key_map.json`. Recibe `list[Habit]`, no JSON. Los hábitos nuevos reclaman la tecla libre más baja en orden `(order, id)` y **nunca se reasignan**; los que desaparecen liberan su tecla; sin teclas libres se registra `KFUL` y se omite. **`update_mapping` solo debe llamarse tras un `get_habits()` exitoso** — llamarlo tras un fallo liberaría las teclas de hábitos que siguen existiendo.
- **`health.py`** — `classify(exc)` decide el destino de un fallo: `"key"` (excepciones `Provider*` → `AUTH`/`NET`/`API`, se pintan en rojo) o `"file"` (fallos del propio Stream Deck, solo a fichero). `log_failure` → `checkin_failures.log` (JSON-lines); `log_device_error` → `device_errors.log` (texto plano).
- **`error_codes.py`** — `CODES`: los cuatro códigos cortos que caben en una tecla.
- **`emoji.py`** — `extract_emoji(text) -> (emoji, resto)`, cubriendo variation selectors y ZWJ. Sin dependencias de Pillow ni de ningún proveedor, para poder usarse desde cualquier adaptador.

## El bucle principal

`main()` construye el proveedor (`sys.exit(1)` si falla), abre la sesión del deck, carga el mapeo y entra en un bucle infinito de `refresh_cycle()` separados por `refresh_event.wait(timeout=REFRESH_SECONDS)` (900s).

**Cada ciclo (`refresh_cycle`):**

1. Pinta las teclas reservadas.
2. `provider.get_habits()` — una sola petición; ya trae el progreso de hoy en `current_value`, no hay ninguna fecha que decidir en el cliente. Si lanza `ProviderError`: pinta el código en todas las teclas mapeadas y **sale del ciclo sin tocar el mapeo**.
3. `key_map.update_mapping()` → `renderer.render_all()`.
4. **Re-registra** `deck.set_key_callback(...)` con un closure fresco sobre el mapeo actual.

El estado que ve el callback es un wrapper dict de una entrada (`habits_ref = {"value": {id: Habit}}`) precisamente para que el closure observe las actualizaciones de ciclos posteriores. Aun así, **el mapeo tecla→hábito solo es tan reciente como el último ciclo.**

**Al pulsar una tecla (`make_key_callback`):**

1. Si es reservada → `deck.keys.handle_key_press` y fin.
2. `pending_requests` + `state_lock` descartan una segunda pulsación del mismo hábito mientras hay una en vuelo. Si `habit is None` (caso defensivo entre ciclos), se ignora.
3. `provider.step(habit)` — **sin comprobar si ya alcanzó el objetivo**: la tecla nunca se bloquea, es la base quien decide el nuevo valor.
4. Éxito → `habit.current_value = new_value` (mutación directa del objeto compartido con `habits_ref`) y repintado inmediato, optimista, sin refetch.
5. `ProviderError` → tecla en rojo con el código + entrada en `checkin_failures.log`.
6. Cualquier otra excepción → se trata como fallo de dispositivo: **nunca se muestra en tecla**, se registra en `device_errors.log` y dispara `session.reconnect()`.

Pulsar la tecla de refresco activa el `Event` desde el hilo de callbacks y despierta el bucle para un ciclo inmediato: el proveedor sigue siendo la única fuente de verdad, solo cambia *cuándo* se le pregunta. `Event.set()` es idempotente.

## Teclas reservadas

De las 15 teclas, `RESERVED_KEYS = {0, 5, 10}` no se asignan a hábitos; quedan 12 para hábitos (`AVAILABLE_KEYS`).

| Tecla | Constante | Acción |
|---|---|---|
| 0 | `KEY_REFRESH` | Fuerza un `refresh_cycle` inmediato |
| 5 | — | Placeholder gris, sin acción. Libre para configuración futura |
| 10 | `KEY_SHUTDOWN` | Apaga la Raspberry Pi. Se pinta distinta (rojo, "APAGAR") porque es destructiva |

### Apagado desde la tecla 10

`KEY_SHUTDOWN` ejecuta `sudo -n shutdown -h now` (`deck/keys.py::_shutdown_pi`). El servicio corre como `admin` **sin sudo**, así que hace falta una regla `NOPASSWD` en la Pi para que el comando no se quede pidiendo contraseña (el `-n` hace que falle rápido en vez de bloquear si la regla no está). Como cualquier cambio de sudoers requiere contraseña interactiva, no se puede aplicar por SSH no interactivo desde esta máquina — hay que ejecutarlo a mano en la Pi (o por SSH interactivo):

```bash
echo 'admin ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now' | sudo tee /etc/sudoers.d/streamdeck-habits-shutdown
sudo chmod 440 /etc/sudoers.d/streamdeck-habits-shutdown
```

Si falta la regla, la pulsación no apaga nada y el fallo queda solo en `device_errors.log` — la tecla no da ninguna señal, igual que el resto de errores de dispositivo.

## Operar la Raspberry Pi

Hay acceso SSH sin contraseña (clave pública ya autorizada) desde esta máquina: `ssh admin@RP3-MotoComm-1.local`. Puedes lanzar comandos directamente en la Pi (logs, estado del servicio, reinicio, despliegue, verificación) sin depender del usuario.

La resolución mDNS del `.local` es a veces intermitente: si un comando falla con "Could not resolve hostname", **reintenta antes de darlo por caído**.

**Confirma con el usuario antes de desplegar o reiniciar el servicio**, salvo que ya lo haya pedido explícitamente en el mismo turno: es producción. La verificación previa (compilar/importar en un temporal) no afecta al servicio y no necesita confirmación.

### Verificación previa al despliegue

Lo más parecido a un test disponible desde esta máquina. No toca el servicio en marcha: copia los `.py` a un directorio temporal en la Pi y los compila/importa con el venv real (Python 3.13.5, con todas las dependencias).

```bash
# Sintaxis (byte-compila, no ejecuta nada):
tar cf - $(find . -name '*.py' -not -path './venv/*') | ssh admin@RP3-MotoComm-1.local \
  'd=$(mktemp -d); tar xf - -C "$d"; /opt/streamdeck-habits/venv/bin/python -m compileall -q "$d" && echo SYNTAX_OK; rc=$?; rm -rf "$d"; exit $rc'

# Imports (detecta imports circulares, NameError, etc.). Seguro porque main() está bajo
# `if __name__ == "__main__"`, así que importar no arranca el daemon ni necesita un deck
# conectado (importar DeviceManager no requiere hardware):
tar cf - $(find . -name '*.py' -not -path './venv/*') | ssh admin@RP3-MotoComm-1.local \
  'd=$(mktemp -d); tar xf - -C "$d"; cd "$d"; /opt/streamdeck-habits/venv/bin/python -c "import orchestrator, provider.base, provider.supabase, core.key_map, core.health, core.error_codes, core.emoji, deck.session, deck.primitives, deck.renderer, deck.keys; print(\"IMPORTS_OK\")"; rc=$?; cd /; rm -rf "$d"; exit $rc'
```

### Probar código sin mergear (`deploy.sh --test`)

Para validar en la Pi un cambio del árbol de trabajo local **antes de commitear/mergear a `main`**, sin que `git pull` machaque el código de prueba:

1. **Copiar** el árbol de trabajo (solo versionado + nuevos no ignorados; nunca `.env`, `venv/`, logs ni `habit_key_map.json`) sobre `/opt/streamdeck-habits`:

   ```bash
   tar cf - $(git ls-files -c -o --exclude-standard) | ssh admin@RP3-MotoComm-1.local 'tar xf - -C /opt/streamdeck-habits'
   ```

   Deja el árbol git de la Pi "sucio" respecto a `main`, pero es reversible. Al venir de Windows los ficheros llegan con CRLF (inocuo para Python; `.gitattributes` fuerza LF en los `.sh`/`.service` para que los scripts no se rompan).

2. **Reiniciar** con el código copiado, sin `git pull`:

   ```bash
   ssh admin@RP3-MotoComm-1.local 'bash /opt/streamdeck-habits/deploy/deploy.sh --test'
   ```

3. **Observar** que arranca limpio: PID nuevo estable, sin errores en el journal ni en `checkin_failures.log`/`device_errors.log`, y comportamiento correcto en el hardware.

4. **Si va bien** → commit + push a `main`; luego en la Pi `git -C /opt/streamdeck-habits fetch && git -C /opt/streamdeck-habits reset --hard origin/main` (deja `main` limpio con LF) y despliegue normal.
   **Si va mal** → en la Pi `git -C /opt/streamdeck-habits checkout -- .` (restaura `main`) y `deploy/deploy.sh --test`. Si el código de prueba llegó a crashear en bucle y systemd lo dejó parado, arrancarlo de nuevo sí necesita sudo: `sudo systemctl start streamdeck-habits.service`.

### Despliegue

```bash
ssh admin@RP3-MotoComm-1.local "bash /opt/streamdeck-habits/deploy/deploy.sh"
```

`deploy.sh` hace `git pull` y mata el proceso principal para que systemd lo relance por `Restart=on-failure` con el código nuevo — **sin sudo**, por eso funciona en SSH no interactivo. Con `--test` hace lo mismo sin el `git pull`.

- **No instala cambios de la unit de systemd.** Si tocas `deploy/streamdeck-habits.service`, cópialo a mano a `/etc/systemd/system/` con `sudo` + `daemon-reload` + `restart`.
- Existe un alias `habits-update` en el `~/.bashrc` de la Pi para uso interactivo, pero **no funciona desde `ssh host "habits-update"`**: el `.bashrc` de Debian corta la ejecución al inicio si el shell no es interactivo (`case $- in *i*) ;; *) return;; esac`), así que el alias nunca llega a definirse y falla con `command not found`. Usa siempre la ruta completa al script.

### Disposición en la Pi

`config.py` fija `BASE_DIR = "/opt/streamdeck-habits"` y el shebang de `orchestrator.py` apunta a `/opt/streamdeck-habits/venv/bin/python` (Python 3.13.5). **No hay override por variable de entorno: trata `BASE_DIR` como fijo.** Junto al código se espera (todo gitignored salvo `.env.example`):

- `.env` — `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` (cargados vía `python-dotenv` en `SupabaseProvider.__init__`)
- `habit_key_map.json` — mapeo `habit_id -> key_index`, se crea y actualiza solo
- `checkin_failures.log` — JSON-lines de checkins fallidos hacia Supabase (errores de API, tecla en rojo)
- `device_errors.log` — texto plano de fallos del propio dispositivo (nunca se muestran en tecla)

## Ejecutar y dependencias

No hay `requirements.txt` ni lockfile: los cuatro imports de terceros se instalan a mano en un venv.

```bash
pip install streamdeck python-dotenv requests Pillow
```

- `python orchestrator.py` — daemon principal (requiere una Stream Deck real conectada y un `.env` válido)
- `python scripts/test_hw.py` — smoke test de hardware: enumera el deck, imprime modelo/serie/firmware, registra pulsaciones crudas
- `python scripts/toggle_test.py` — smoke test visual: alterna cada tecla entre azul y verde al pulsarla, sin llamadas de red

Los tres necesitan que la librería `StreamDeck` (python-elgato-streamdeck) tenga acceso al dispositivo USB físico, así que solo tienen sentido en el hardware objetivo.

Para pintar el emoji del nombre de un hábito como icono a color hace falta además la fuente del sistema `fonts-noto-color-emoji` (paquete Debian, no de Python): `sudo apt install fonts-noto-color-emoji` — requiere sudo interactivo, no se puede instalar por SSH no interactivo desde aquí. Si falta, `deck/primitives.py::_emoji_font()` devuelve `None` y la tecla simplemente no pinta icono: **se degrada, no falla**.

## Estilo y convenciones

El estándar está en `pyproject.toml` (ver también `.claude/skill/SKILL_Python_Code_Style_&_Documentation.md`):

- **`ruff`** — lint + formato (línea 120, comillas dobles, isort/pyupgrade/bugbear/simplify): `ruff check --fix .` y `ruff format .`
- **`mypy`** — comprobación de tipos (`target py313`; `StreamDeck.*` con `ignore_missing_imports` porque no publica stubs): `mypy .`
- **Type hints** en todas las APIs públicas, con `from __future__ import annotations` al principio de cada módulo (evaluación diferida: la anotación nunca se evalúa en runtime, así que es segura aunque apunte a algo no importado).
- **Docstrings estilo Google** (Args/Returns/Raises) en clases y funciones públicas.
- **Comentarios y mensajes de log en español.**
- **Todos los `print` con `flush=True`**: esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).

## Mantén este fichero al día

Si al hacer un cambio descubres que algo descrito aquí no coincide con el comportamiento real del código (una descripción desactualizada, un comando que ya no funciona como se documenta, un color, una tecla o un valor que cambió), corrígelo **en el mismo turno** en vez de dejarlo pasar. Este documento solo es útil si las próximas sesiones pueden confiar en él sin verificarlo todo.
