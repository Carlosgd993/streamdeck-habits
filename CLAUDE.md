# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un daemon Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos y tareas. Corre 24/7 como servicio systemd en una Raspberry Pi 3, sondea la base de datos vía PostgREST cada 15 minutos y pinta cada tecla: los **hábitos** en blanco (pendiente) o gris oscuro (hecho hoy), registrando un checkin al pulsarlos; las **tareas** pendientes en el color de su prioridad, cerrándose al pulsarlas.

La base de datos vive en el repo hermano `../habits-core` (repositorio Git independiente). Este daemon es deliberadamente **tonto**: no calcula qué día es hoy, ni el siguiente valor de un hábito, ni qué tareas tocan, ni si algo está bloqueado. Todo eso lo decide la base. Ver [../CLAUDE.md](../CLAUDE.md) para el contexto que atraviesa ambos repos.

**No hay tests automatizados ni lockfile de dependencias, y nada de esto se ejecuta en la máquina de desarrollo**: no tiene Python usable (solo el stub de la Microsoft Store) ni el hardware. Todo se verifica en la Pi por SSH — ver [Operar la Raspberry Pi](#operar-la-raspberry-pi).

## Mapa del código

Son ~1.500 líneas en total: leer un módulo entero es barato, la duda suele ser cuál.

```
orchestrator.py   269  Punto de entrada (ExecStart de systemd). Bucle y callbacks de tecla.
config.py          22  Rutas, teclas reservadas, intervalo de refresco.

provider/              LA API, aislada tras dos puertos. No sabe nada del deck.
  base.py         269  Los puertos: HabitProvider y TaskProvider; Habit/BooleanHabit/RealHabit,
                       Task, excepciones.
  supabase.py     266  El adaptador (implementa ambos): PostgREST, build_habit(), build_task().
                       Único sitio con detalles de Supabase.

deck/                  EL HARDWARE. No sabe nada del proveedor de datos.
  session.py       76  Abrir/cerrar/reconectar el dispositivo, brillo.
  renderer.py     170  Pintado de alto nivel: render_habit, render_task, render_all…
  primitives.py   128  Pillow de bajo nivel: solid_tile, text_tile, fuente de emoji.
  keys.py          50  Teclas reservadas y qué hace cada una.
  style.py         42  Colores y tamaños de fuente. Nada más.

core/                  DOMINIO. Agnóstico de ambos lados.
  key_map.py      105  Asignación persistente hábito → tecla y volátil tarea → tecla.
  health.py        56  Clasificar un fallo: ¿tecla en rojo o solo log?
  error_codes.py   14  AUTH / NET / API / KFUL.
  emoji.py         40  extract_emoji(): separa el primer emoji de una cadena.

deploy/deploy.sh       Despliegue en la Pi (normal y --test).
deploy/*.service       Unit de systemd. No se instala sola.
scripts/               Dos smoke tests de hardware. Requieren un deck conectado.
```

| Si vas a cambiar… | Toca | Ojo con |
|---|---|---|
| El aspecto de una tecla (color, tamaño) | `deck/style.py` | Nada más; el resto solo consume esas constantes |
| Los colores de prioridad de las tareas | `deck/style.py` → `COLOR_TASK_BY_PRIORITY` | Solo existen las prioridades 0/1/3/5; el resto cae a la 0 |
| Qué texto o icono muestra un hábito o una tarea | `provider/base.py` → `display_label()` | Es del dominio, no del pintado |
| Qué hace una tecla reservada | `config.py` + `deck/keys.py` | Ver [Teclas reservadas](#teclas-reservadas) |
| Cómo se habla con la base | `provider/supabase.py` **y solo ahí** | Si tocas otro sitio, has roto el aislamiento |
| Qué campos trae un hábito o una tarea | `provider/supabase.py::build_habit`/`build_task` + `provider/base.py` | La columna debe existir ya en la vista de `habits-core` |
| Cuándo se refresca o qué pasa al pulsar | `orchestrator.py` | Ver [El bucle principal](#el-bucle-principal) |
| Cómo se reparten las teclas | `core/key_map.py` | Los hábitos persisten su tecla, las tareas no. Ver [Reparto de teclas](#reparto-de-teclas) |
| Qué se muestra al fallar algo | `core/health.py` + `core/error_codes.py` | Los fallos de dispositivo nunca van a tecla |
| Añadir una capacidad nueva (undo, omitir…) | **Primero** el contrato en `../habits-core` | El daemon no inventa lógica |

## Arquitectura

Tres capas por carpeta, con una regla de dependencia estricta: **`provider/` no sabe nada del Stream Deck; `deck/` no sabe nada del proveedor de datos; `core/` y `orchestrator.py` orquestan ambos hablando solo con abstracciones.** `orchestrator.py` y `config.py` viven en la raíz porque los comparten las tres capas (el primero es además el `ExecStart` de systemd).

El eje del diseño es un **puerto/adaptador (hexagonal)**: `provider/base.py` define los puertos abstractos y Supabase es solo un adaptador detrás de ellos. Cambiar de backend = escribir otro `HabitProvider`/`TaskProvider` y tocar **una línea** en `orchestrator.main()` (`SupabaseProvider()`); ni `core/`, ni `deck/`, ni el resto del orquestador cambian.

### `provider/` — la API tras dos puertos

- **`base.py`** — los **puertos**. Contiene todo lo que el resto del proyecto necesita saber de "la API", sin acoplarse a ningún backend:
  - **`HabitProvider`** (ABC): `get_habits() -> list[Habit]` (ya trae el progreso de hoy) y `step(habit) -> float` (avanza un paso, devuelve el nuevo total).
  - **`TaskProvider`** (ABC): `get_tasks() -> list[Task]` (solo pendientes, **ya ordenadas** por el proveedor) y `complete_task(task) -> None`. Está **separado a propósito** de `HabitProvider`: son dos capacidades distintas y un backend puede ofrecer una sin la otra. `SupabaseProvider` implementa las dos porque salen del mismo contrato.
  - **Excepciones agnósticas**: `ProviderError` → `ProviderAuthError`, `ProviderNetworkError`, `ProviderDataError`. Compartidas por ambos puertos, así que `core.health` y el pintado de errores sirven igual para los dos.
  - **`Habit`** (ABC), construido desde campos ya parseados, **no** desde JSON crudo: `id`, `name`, `emoji`, `order` (pista para asignar teclas), `current_value` (progreso de hoy, **puede superar `goal`** — 10/8 es válido); propiedades `goal` (default `1.0`) e `is_done` (`current_value >= goal`, que **solo decide el color de la tecla, no bloquea nada**); `display_label()` abstracto. `BooleanHabit` → muestra el nombre. `RealHabit` (cuantificables, con `goal`/`step`/`unit`) → muestra solo el progreso (`"3/8 Cups"`, o `"10/8 Cups"` por encima del objetivo, sin decimales feos en enteros).
  - **`Task`** — concreta y sin subtipos, al revés que `Habit`: una tarea está pendiente o deja de existir, no tiene estado "hecha" que pintar. Campos: `id` (el de la **ocurrencia**, es lo que se envía para cerrarla), `title`, `priority` (solo `0`/`1`/`3`/`5`, no son contiguos), `overdue` (arrastra de un día anterior), `due_day`. `display_label()` devuelve el título recortado a `TITLE_MAX_CHARS` con elipsis. **No tiene emoji**: las tareas no guardan icono, se distinguen por el color de su prioridad.
- **`supabase.py`** — el **adaptador**; concentra todo lo específico del backend:
  - Lee `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` del `.env`; `__init__` lanza `ProviderAuthError` si falta alguna. Base PostgREST `<url>/rest/v1`, cabeceras `apikey` + `Authorization: Bearer`.
  - `get_habits`: **una sola petición** a `v_today_habits` (la vista ya filtra activos y trae el progreso), con `order=sort_order` porque la vista no ordena por sí sola.
  - `step`: `POST /rpc/habit_step` con `{"p_habit_id": habit.id}`. Atómico en la base (Boolean → salta a `goal`; Real → suma `step` sin tope). Devuelve el nuevo total.
  - `get_tasks`: **una sola petición** a `v_today_tasks`, que ya excluye completadas y omitidas y arrastra las vencidas. Tampoco ordena por sí sola: el orden va explícito en `_TASKS_ORDER` (`priority.desc,due_date.asc`).
  - `complete_task`: `POST /rpc/complete_task` con `{"p_task_id": task.id}`. La función devuelve `void`, así que responde **204 sin cuerpo**: no hay JSON que parsear y el status es toda la confirmación. Es idempotente en la base, reintentar es seguro.
  - Traduce `RequestException` / 401-403 / no-2xx / JSON inválido a las excepciones `Provider*`. Un 401/403 puede ser clave inválida **o** un `GRANT` que falta en el contrato; el mensaje lo insinúa.
  - `build_habit(data)`: parsea `icon_res` (`"txt_<emoji>"` → emoji vía `core.emoji.extract_emoji`; los predefinidos tipo `"habit_water"` dan emoji vacío), enruta `type == "Real"` a `RealHabit` y todo lo demás (incluido `"Boolean"` y valores desconocidos) a `BooleanHabit`, toma `order` de `sort_order`. `build_task(data)` es el equivalente para tareas, sin ramas: la vista solo devuelve pendientes.
  - Las tablas (`habits`, `habit_checkins`, `tasks`, …) están cerradas con RLS y no son accesibles con la clave publishable: este adaptador nunca las menciona. El contrato está en `../habits-core/docs/contrato.md`; hay un resumen orientado a cliente en `.claude/tables-doc.md`, el porqué de la regla en `.claude/estructura-bd.md` y peticiones de ejemplo en `.claude/supabase.http`.

### `deck/` — hardware y pintado

- **`session.py`** — `DeckSession`: apertura (30 reintentos cada 2s; `sys.exit(1)` si no aparece), cierre, `reconnect()` y brillo (`BRIGHTNESS = 60`). `reconnect()` solo se usa tras un fallo en marcha, nunca en el arranque inicial.
- **`renderer.py`** — alto nivel, sobre una tecla o el deck completo: `render_habit` (usa `display_label()`, `emoji` e `is_done` para elegir blanco/pendiente o gris/hecho — solo color, sin bloquear nada), `render_task` (color de fondo según `priority`; una prioridad desconocida cae a la 0 en vez de fallar), `render_task_sending` (verde vivo + ✔, el acuse de recibo de la pulsación), `render_checkin_error`, `render_reserved`, `render_shutdown` (fondo rojo de aviso, "APAGAR" e icono 🔴, para distinguirla de una reservada normal), `render_empty`, `render_all` (repinta las 15 resolviendo cada tecla como reservada → hábito → tarea → vacía), `render_error_all` (pinta el código de error en las teclas de **un** mapeo, el de hábitos o el de tareas según cuál haya fallado, para no dejar información obsoleta en pantalla).
- **`primitives.py`** — Pillow de bajo nivel vía `PILHelper`: `solid_tile` (color plano) y `text_tile` (texto envuelto/centrado más un `emoji` opcional como icono a color en la mitad superior, o la tecla entera si `text` es vacío). El emoji usa `_emoji_font()` (`NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos porque es una fuente CBDT/CBLC de mapa de bits) y `_emoji_glyph()` (`embedded_color=True`, reescalado).
- **`keys.py`** — ver [Teclas reservadas](#teclas-reservadas).
- **`style.py`** — todos los colores (`COLOR_*`) y tamaños de fuente (`FONT_SIZE_*`). Es la capa de pintado: no sabe de hábitos ni del proveedor. Incluye `COLOR_TASK_BY_PRIORITY` / `COLOR_TEXT_TASK_BY_PRIORITY` (dicts indexados por las prioridades `0`/`1`/`3`/`5`) y `COLOR_TASK_SENDING`. Ojo: el rojo de prioridad 5 es **distinto** de `COLOR_ERROR` a propósito, para que una tarea urgente no se confunda con una tecla en error; y una tarea de prioridad 0 es blanca, igual que un hábito pendiente.

### `core/` — dominio agnóstico

- **`key_map.py`** — dos repartos con reglas opuestas:
  - `update_mapping` persiste el mapeo hábito→tecla en `habit_key_map.json`. Recibe `list[Habit]`, no JSON. Los hábitos nuevos reclaman la tecla libre más baja en orden `(order, id)` y **nunca se reasignan**; los que desaparecen liberan su tecla; sin teclas libres se registra `KFUL` y se omite. **Solo debe llamarse tras un `get_habits()` exitoso** — llamarlo tras un fallo liberaría las teclas de hábitos que siguen existiendo.
  - `assign_task_keys` reparte entre las tareas las teclas que los hábitos dejan libres. **No persiste nada y no reconcilia**: las tareas son volátiles (nacen y se cierran durante el día), así que cada ciclo se reparten de cero en el orden en que llegan del proveedor. Los hábitos siempre tienen preferencia. Ver [Reparto de teclas](#reparto-de-teclas).
- **`health.py`** — `classify(exc)` decide el destino de un fallo: `"key"` (excepciones `Provider*` → `AUTH`/`NET`/`API`, se pintan en rojo) o `"file"` (fallos del propio Stream Deck, solo a fichero, sin código). `log_failure(item_id, detail, kind)` → `checkin_failures.log` (JSON-lines; `kind` es `"habit"` o `"task"`); `log_device_error` → `device_errors.log` (texto plano).
- **`error_codes.py`** — `CODES`: los cuatro códigos cortos que caben en una tecla.
- **`emoji.py`** — `extract_emoji(text) -> (emoji, resto)`, cubriendo variation selectors y ZWJ. Sin dependencias de Pillow ni de ningún proveedor, para poder usarse desde cualquier adaptador.

## El bucle principal

`main()` construye el proveedor (`sys.exit(1)` si falla), abre la sesión del deck, carga el mapeo y entra en un bucle infinito de `refresh_cycle()` separados por `refresh_event.wait(timeout=REFRESH_SECONDS)` (900s). El mismo `SupabaseProvider` se usa por sus dos puertos (`habit_provider` / `task_provider`), solo para dejar explícito qué capacidad usa cada llamada.

**Cada ciclo (`refresh_cycle`):**

1. Pinta las teclas reservadas.
2. `get_habits()` — una sola petición; ya trae el progreso de hoy en `current_value`, no hay ninguna fecha que decidir en el cliente. Éxito → `key_map.update_mapping()`.
3. `get_tasks()` — otra sola petición; solo pendientes y ya ordenadas. Éxito → `key_map.assign_task_keys()` con el mapeo de hábitos **ya reconciliado**, porque las teclas de tarea salen de las que los hábitos dejan libres.
4. `renderer.render_all()` con ambos mapeos.
5. Los códigos de error de las lecturas que fallaron se pintan **después** de `render_all`, para que tapen los datos viejos en vez de que el repintado los borre a ellos.
6. **Re-registra** `deck.set_key_callback(...)` con un closure fresco sobre los mapeos actuales.

**Las dos lecturas fallan por separado.** Un fallo leyendo tareas pinta su código solo en las teclas de tarea y deja los hábitos intactos, y al revés: la lectura que falla no toca su mapeo ni sus datos (se conservan los del ciclo anterior), y desde luego no toca los de la otra. El ciclo **siempre** llega a `render_all` y a re-registrar el callback, de modo que lo pintado y lo que hace cada tecla nunca se desincronizan.

El estado que ve el callback son wrappers dict de una entrada (`habits_ref`/`tasks_ref = {"value": {id: obj}}`) precisamente para que el closure observe las actualizaciones de ciclos posteriores. Aun así, **el mapeo tecla→elemento solo es tan reciente como el último ciclo.**

**Al pulsar una tecla (`make_key_callback`):**

1. Si es reservada → `deck.keys.handle_key_press` y fin.
2. Se resuelve la tecla a un hábito o a una tarea (nunca a ambos: los mapeos no se solapan). `pending_requests` + `state_lock` (vía `_claim`/`_release`) descartan una segunda pulsación del mismo elemento mientras hay una en vuelo.
3. **Hábito** → `provider.step(habit)`, **sin comprobar si ya alcanzó el objetivo**: la tecla nunca se bloquea, es la base quien decide el nuevo valor. Éxito → `habit.current_value = new_value` (mutación directa del objeto compartido con `habits_ref`) y repintado inmediato, optimista, sin refetch.
4. **Tarea** → primero `render_task_sending` (verde vivo + ✔, acuse de recibo inmediato), luego `complete_task(task)`. Solo cuando la base **confirma** (204), la tecla se apaga y la tarea sale de `tasks_ref` para que otra pulsación no reintente cerrarla. Las demás teclas **no se mueven**: las tareas restantes se recolocan en el siguiente ciclo, nunca bajo el dedo.
5. `ProviderError` → tecla en rojo con el código + entrada en `checkin_failures.log`.
6. Cualquier otra excepción → se trata como fallo de dispositivo: **nunca se muestra en tecla**, se registra en `device_errors.log` y dispara `session.reconnect()`. Los repintados van envueltos en `_safe_render` justo por esto.

Pulsar la tecla de refresco activa el `Event` desde el hilo de callbacks y despierta el bucle para un ciclo inmediato: el proveedor sigue siendo la única fuente de verdad, solo cambia *cuándo* se le pregunta. `Event.set()` es idempotente.

## Reparto de teclas

De las 15 teclas, `RESERVED_KEYS = {0, 5, 10}` no se asignan a nada; quedan 12 (`AVAILABLE_KEYS`) que **comparten hábitos y tareas**, con reglas deliberadamente distintas:

- **Los hábitos van primero** y su tecla es estable: se persiste en `habit_key_map.json` y no se reasigna nunca mientras el hábito exista.
- **Las tareas ocupan lo que quede**, de menor a mayor índice, recalculado en cada ciclo. Una tarea nunca pisa la tecla de un hábito. Lo que no cabe se registra como `KFUL` y no se pinta.

Con los 5 hábitos actuales eso deja los hábitos en las teclas 1-4 y 6, y las tareas a partir de la 7.

Las tareas se pintan con el color de su prioridad (`deck/style.py`): **0 blanca, 1 verde, 3 amarilla, 5 roja**. No llevan icono, así que una tarea de prioridad 0 se ve igual que un hábito booleano pendiente — es una consecuencia asumida del esquema de color elegido, no un descuido.

## Teclas reservadas

| Tecla | Constante | Acción |
|---|---|---|
| 0 | `KEY_REFRESH` | Fuerza un `refresh_cycle` inmediato |
| 5 | — | Placeholder gris, sin acción. Libre para configuración futura |
| 10 | `KEY_SHUTDOWN` | Apaga la Raspberry Pi. Se pinta distinta (rojo, "APAGAR") porque es destructiva |

### Apagado desde la tecla 10

`KEY_SHUTDOWN` ejecuta `sudo -n shutdown -h now` (`deck/keys.py::_shutdown_pi`). El servicio corre como `admin` **sin sudo**, así que hace falta una regla `NOPASSWD` en la Pi para que el comando no se quede pidiendo contraseña (el `-n` hace que falle rápido en vez de bloquear si la regla no está):

```bash
echo 'admin ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now' | sudo tee /etc/sudoers.d/streamdeck-habits-shutdown
sudo chmod 440 /etc/sudoers.d/streamdeck-habits-shutdown
```

**Regla ya aplicada en la Pi desplegada: el botón apaga sin problema.** Si algún día hay que reinstalar la Pi desde cero o mover el servicio a otra máquina, hay que reaplicar esta regla a mano (requiere contraseña interactiva, no se puede por SSH no interactivo desde aquí) o el botón fallará silenciosamente y quedará solo en `device_errors.log`, sin señal en la tecla, igual que el resto de errores de dispositivo.

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

4. **Si va bien** → commit + push a `main`; luego despliegue normal (`deploy/deploy.sh` sin `--test`, ver abajo) para que la Pi quede en `origin/main` con el árbol limpio.
   **Si va mal** → en la Pi `git -C /opt/streamdeck-habits checkout -- .` (restaura `main`) y `deploy/deploy.sh --test`. Si el código de prueba llegó a crashear en bucle y systemd lo dejó parado, arrancarlo de nuevo sí necesita sudo: `sudo systemctl start streamdeck-habits.service`.

**No dejes el paso 4 a medias.** Un `--test` que "fue bien" pero nunca se cerró con el despliegue normal deja la Pi con el árbol sucio y por detrás de `origin/main` indefinidamente — el servicio sigue corriendo con lo que haya en disco (puede coincidir con `main` por casualidad, o no) y nadie se entera hasta que alguien compara `git status`/`git log` a mano. Verificarlo cuesta un comando: `ssh admin@RP3-MotoComm-1.local 'cd /opt/streamdeck-habits && git status --short && git log --oneline -1'` debe salir vacío y en el mismo commit que `origin/main`.

### Cambiar de proyecto Supabase (main / test)

Hay dos proyectos Supabase activos, cada uno con su propia base (esquema
identico, datos independientes):

| Alias | Proyecto | ref | URL |
|---|---|---|---|
| `main` | `habits-core` (produccion) | `ufyzpixnhrsltoxdqihn` | `https://ufyzpixnhrsltoxdqihn.supabase.co` |
| `test` | `habits-core-test` | `dkomeqbvhobkaulogibw` | `https://dkomeqbvhobkaulogibw.supabase.co` |

El `.env` de la Pi trae las credenciales de **los dos** proyectos a la vez:
`SUPABASE_URL`/`SUPABASE_PUBLISHABLE_KEY` para `main` (sin sufijo, es el caso
normal) y `SUPABASE_URL_TEST`/`SUPABASE_PUBLISHABLE_KEY_TEST` para `test` (ver
`.env.example`). `SUPABASE_ENV` (`main` o `test`) dice cual de las dos lee
`provider/supabase.py`.
Cambiar de proyecto es cambiar esa variable y reiniciar el servicio, nada mas
— no hay script ni fichero nuevo:

```bash
ssh admin@RP3-MotoComm-1.local "sed -i 's/^SUPABASE_ENV=.*/SUPABASE_ENV=test/' /opt/streamdeck-habits/.env"
ssh admin@RP3-MotoComm-1.local 'kill -9 $(systemctl show -p MainPID --value streamdeck-habits.service)'
```

(cambia `test` por `main` para volver a produccion). El `kill -9` es intencional:
systemd relanza el proceso por `Restart=on-failure` y ahi relee el `.env`
actualizado — es la misma tecnica que usa `deploy.sh`. **Cuando el usuario
diga "apunta la pi a test" o "a main/produccion", es literalmente esos dos
comandos** — no hace falta preguntar cual proyecto es cual, ya esta en la
tabla de arriba.

### Despliegue

```bash
ssh admin@RP3-MotoComm-1.local "bash /opt/streamdeck-habits/deploy/deploy.sh"
```

`deploy.sh` hace `git pull` y mata el proceso principal para que systemd lo relance por `Restart=on-failure` con el código nuevo — **sin sudo**, por eso funciona en SSH no interactivo. Con `--test` hace lo mismo sin el `git pull`.

- **No instala cambios de la unit de systemd.** Si tocas `deploy/streamdeck-habits.service`, cópialo a mano a `/etc/systemd/system/` con `sudo` + `daemon-reload` + `restart`.
- Existe un alias `habits-update` en el `~/.bashrc` de la Pi para uso interactivo, pero **no funciona desde `ssh host "habits-update"`**: el `.bashrc` de Debian corta la ejecución al inicio si el shell no es interactivo (`case $- in *i*) ;; *) return;; esac`), así que el alias nunca llega a definirse y falla con `command not found`. Usa siempre la ruta completa al script.

### Disposición en la Pi

`config.py` fija `BASE_DIR = "/opt/streamdeck-habits"` y el shebang de `orchestrator.py` apunta a `/opt/streamdeck-habits/venv/bin/python` (Python 3.13.5). **No hay override por variable de entorno: trata `BASE_DIR` como fijo.** Junto al código se espera (todo gitignored salvo `.env.example`):

- `.env` — credenciales de **ambos** proyectos (`SUPABASE_URL`/`SUPABASE_PUBLISHABLE_KEY` para main, `_TEST` para test) más `SUPABASE_ENV` para elegir cuál usa `SupabaseProvider.__init__` (vía `python-dotenv`) — ver [Cambiar de proyecto Supabase](#cambiar-de-proyecto-supabase-main--test)
- `habit_key_map.json` — mapeo `habit_id -> key_index`, se crea y actualiza solo
- `checkin_failures.log` — JSON-lines de escrituras fallidas hacia Supabase, checkins de hábito y cierres de tarea (errores de API, tecla en rojo); el campo `kind` distingue `"habit"` de `"task"`
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
