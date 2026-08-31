# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es esto

Un daemon Python que convierte una Elgato Stream Deck física en un mando de seguimiento de hábitos y tareas. Corre 24/7 como servicio systemd en una Raspberry Pi 3, sondea la base de datos vía PostgREST cada 15 minutos y navega por un **menú de pantallas** (ver [Menú y pantallas](#menú-y-pantallas)): la vista por defecto "Hoy" pinta hábitos en blanco (pendiente) o gris oscuro (hecho hoy), registrando un checkin al pulsarlos, y tareas pendientes en el color de su prioridad, cerrándose al pulsarlas; desde el menú también se llega a vistas filtradas ("Hábitos" —  donde pulsar un booleano ya hecho lo **deshace** —, "Tareas"), a "Crear" (crea una tarea a partir de una plantilla) y a un submenú "Sistema" con la suspensión de la pantalla y el apagado de la Pi. **Mantener pulsado** un hábito o una tarea (en vez de soltarlo enseguida) abre su **menú de opciones**: el de un hábito sigue siendo un prototipo sin opciones reales, el de una tarea ya permite cambiar su prioridad — ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado). Tras `STANDBY_SECONDS` sin pulsar nada (o pulsando "Suspender") entra en **stand by**: apaga la retroiluminación y deja de sondear, y cualquier tecla lo despierta — ver [Stand by](#stand-by).

La base de datos vive en el repo hermano `../habits-core` (repositorio Git independiente). Este daemon es deliberadamente **tonto**: no calcula qué día es hoy, ni el siguiente valor de un hábito, ni qué tareas tocan, ni si algo está bloqueado. Todo eso lo decide la base. Ver [../CLAUDE.md](../CLAUDE.md) para el contexto que atraviesa ambos repos.

**No hay tests automatizados ni lockfile de dependencias, y nada de esto se ejecuta en la máquina de desarrollo**: no tiene Python usable (solo el stub de la Microsoft Store) ni el hardware. Todo se verifica en la Pi por SSH — ver [Operar la Raspberry Pi](#operar-la-raspberry-pi).

## Mapa del código

Son ~1.500 líneas en total: leer un módulo entero es barato, la duda suele ser cuál.

```
orchestrator.py        Punto de entrada (ExecStart de systemd). Bucle, dispatcher de pantallas y callbacks de tecla.
config.py               Rutas, teclas reservadas, tamaño de página, timeouts de refresco/auto-retorno/stand by.

provider/              LA API, aislada tras tres puertos. No sabe nada del deck ni de pantallas.
  base.py             Los puertos: HabitProvider, TaskProvider y TemplateProvider;
                       Habit/BooleanHabit/RealHabit, Task, Template, excepciones.
  supabase.py         El adaptador (implementa los tres): PostgREST, build_habit(), build_task(),
                       build_template(). Único sitio con detalles de Supabase.
  keepalive.py        Reactivación best-effort de un proyecto Supabase pausado por inactividad (Management
                       API, credencial y API distintas de las de arriba). Ver más abajo, en "El bucle
                       principal".

deck/                  EL HARDWARE. No sabe nada del proveedor de datos.
  session.py          Abrir/cerrar/reconectar el dispositivo, brillo (incluido el 0 del stand by).
  renderer.py         Pintado de alto nivel: render_habit, render_task, render_page (pinta cualquier
                       pantalla ya resuelta por core.screens)…
  primitives.py       Pillow de bajo nivel: solid_tile, text_tile, fuente de emoji.
  keys.py             Efecto lateral de apagado de la Pi (shutdown_pi()), invocado desde Sistema.
  style.py            Colores y tamaños de fuente. Nada más.

core/                  DOMINIO. Agnóstico de ambos lados.
  screens.py          Registro extensible de menú/submenú/vistas y su resolución a teclas, más
                       el teclado numérico, el menú de opciones de mantener pulsado y el stand by.
                       Ver [Menú y pantallas](#menú-y-pantallas).
  key_map.py          Asignación persistente hábito → tecla, volátil tarea → tecla, y paginate().
  health.py           Clasificar un fallo: ¿tecla en rojo o solo log?
  error_codes.py      AUTH / NET / API / KFUL.
  emoji.py            extract_emoji(): separa el primer emoji de una cadena.

deploy/deploy.sh       Despliegue en la Pi: normal (git pull + reinicio) o --test (reinicio a secas,
                       sin git pull). No tiene nada que ver con el proyecto Supabase de test.
deploy/*.service       Unit de systemd. No se instala sola.
scripts/               Dos smoke tests de hardware. Requieren un deck conectado.
```

| Si vas a cambiar… | Toca | Ojo con |
|---|---|---|
| El aspecto de una tecla (color, tamaño) | `deck/style.py` | Nada más; el resto solo consume esas constantes |
| Los colores de prioridad de las tareas | `deck/style.py` → `COLOR_TASK_BY_PRIORITY` | Solo existen las prioridades 0/1/3/5; el resto cae a la 0 |
| Qué texto o icono muestra un hábito, una tarea o una plantilla | `provider/base.py` → `display_label()` | Es del dominio, no del pintado |
| Añadir una vista nueva al menú (p.ej. "por proyecto") | `core/screens.py` → `VIEWS` + `MENU_ENTRIES` | Ver [Menú y pantallas](#menú-y-pantallas). Si la vista muestra códigos de error, además la lista de `view_id` en `orchestrator._paint_current_screen` |
| Qué plantillas ofrece "Crear" | **Nada de código**: la columna `task_templates.show_in_deck` en `../habits-core` | `update task_templates set show_in_deck = true where …`. El daemon solo filtra por ella |
| Qué hábitos abren el teclado numérico en vez de sumar paso | **Nada de código**: la columna `habits.manual_entry` en `../habits-core` (solo con `type='Real'`) | Ver [Menú y pantallas](#menú-y-pantallas), sección "teclado numérico" |
| Qué hace una tecla reservada (menú, paginación, apagado) | `config.py` + `core/screens.py` + `deck/keys.py` | Ver [Menú y pantallas](#menú-y-pantallas) |
| Cuánto tarda la pantalla en apagarse sola | `config.py` → `STANDBY_SECONDS` | Ver [Stand by](#stand-by). Es la única palanca de consumo que tiene el daemon |
| Qué se ve mientras está suspendido | `core/screens.py` → `STANDBY_LAYOUT` | Un dict `tecla → StandbyKey(label, emoji)`; lo que no aparece se pinta negro. El brillo va aparte, en `deck/session.py` → `BRIGHTNESS_STANDBY` |
| Cuánto hay que mantener pulsado un hábito/tarea para abrir su menú de opciones | `config.py` → `LONG_PRESS_SECONDS` | Ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado) |
| Qué opciones reales ofrece el menú de mantener pulsado | `core/screens.py` → `HABIT_OPTIONS_LAYOUT`/`TASK_OPTIONS_LAYOUT` + `resolve_press` | Un dict para habito y otro para tarea — pantallas distintas, opciones distintas. El de habito sigue siendo una maqueta; el de tarea ya tiene "cambiar prioridad" (teclas 11-14). Añadir una opción más es añadir una entrada con su `kind` al layout que toque y darle significado en `resolve_press`, y su pintado en `deck/renderer.py::render_option_entry` |
| Cambiar la prioridad de una tarea desde el deck | `core/screens.py` → `TASK_OPTIONS_LAYOUT` (teclas 11-14) + `provider.base.TaskProvider.set_priority` + `provider/supabase.py` (`rpc/set_task_priority`) | La RPC vive en `../habits-core` (`set_task_priority(p_task_id, p_priority)`, solo toca ocurrencias pendientes) |
| Omitir (skip) una tarea desde el deck | `core/screens.py` → `TASK_OPTIONS_LAYOUT` (tecla 1) + `provider.base.TaskProvider.skip_task` + `provider/supabase.py` (`rpc/skip_task`) | RPC ya existente en `../habits-core` con `grant` a `anon` — cambio solo de cliente, sin tocar la base |
| Cómo se habla con la base | `provider/supabase.py` **y solo ahí** | Si tocas otro sitio, has roto el aislamiento |
| Qué campos trae un hábito, una tarea o una plantilla | `provider/supabase.py::build_habit`/`build_task`/`build_template` + `provider/base.py` | La columna debe existir ya en la vista de `habits-core` |
| Cuándo se refresca o qué pasa al pulsar | `orchestrator.py` | Ver [El bucle principal](#el-bucle-principal) |
| Cómo se reparten las teclas dentro de una vista | `core/key_map.py` + `core/screens.py` | Los hábitos persisten su tecla, las tareas no. Ver [Reparto de teclas](#reparto-de-teclas) |
| Qué se muestra al fallar algo | `core/health.py` + `core/error_codes.py` | Los fallos de dispositivo nunca van a tecla |
| Añadir una capacidad nueva (omitir tareas, corregir un valor…) | **Primero** el contrato en `../habits-core` | El daemon no inventa lógica. Si la RPC ya existe y tiene su `grant` (fue el caso de `habit_undo`), el cambio es solo de cliente: puerto + adaptador + pulsación |

## Arquitectura

Tres capas por carpeta, con una regla de dependencia estricta: **`provider/` no sabe nada del Stream Deck; `deck/` no sabe nada del proveedor de datos; `core/` y `orchestrator.py` orquestan ambos hablando solo con abstracciones.** `orchestrator.py` y `config.py` viven en la raíz porque los comparten las tres capas (el primero es además el `ExecStart` de systemd).

El eje del diseño es un **puerto/adaptador (hexagonal)**: `provider/base.py` define los puertos abstractos y Supabase es solo un adaptador detrás de ellos. Cambiar de backend = escribir otro `HabitProvider`/`TaskProvider`/`TemplateProvider` y tocar **una línea** en `orchestrator.main()` (`SupabaseProvider()`); ni `core/`, ni `deck/`, ni el resto del orquestador cambian.

### `provider/` — la API tras tres puertos

- **`base.py`** — los **puertos**. Contiene todo lo que el resto del proyecto necesita saber de "la API", sin acoplarse a ningún backend:
  - **`HabitProvider`** (ABC): `get_habits() -> list[Habit]` (ya trae el progreso de hoy), `step(habit) -> float` (avanza un paso, devuelve el nuevo total), `undo(habit) -> float` (retrocede: booleano → `0`, cuantificable → `value - step` sin bajar de 0; devuelve 0 si hoy no había nada que deshacer, así que repetirlo es seguro) y `set_value(habit, value) -> float` (fija el valor exacto de hoy — la usa un hábito `manual_entry` tras teclearlo en la pantalla de teclado numérico, ver [Menú y pantallas](#menú-y-pantallas)).
  - **`TaskProvider`** (ABC): `get_tasks() -> list[Task]` (solo pendientes, **ya ordenadas** por el proveedor), `complete_task(task) -> None`, `skip_task(task) -> None` (marca la ocurrencia como omitida, sin devolver nada; sale de `get_tasks()` igual que al completarla) y `set_priority(task, priority) -> None` (cambia la prioridad de una ocurrencia pendiente, sin devolver nada). Las tres últimas comparten forma con `complete_task`; las usa el menú de opciones de una tarea. Está **separado a propósito** de `HabitProvider`: son dos capacidades distintas y un backend puede ofrecer una sin la otra.
  - **`TemplateProvider`** (ABC): `get_templates() -> list[Template]` (solo las de creación rápida, ya ordenadas) y `create_task(template) -> str` (devuelve el id de la ocurrencia nueva). Separado por la misma razón. `SupabaseProvider` implementa los tres porque salen del mismo contrato.
  - **Excepciones agnósticas**: `ProviderError` → `ProviderAuthError`, `ProviderNetworkError`, `ProviderDataError`. Compartidas por los tres puertos, así que `core.health` y el pintado de errores sirven igual para todos.
  - **`Habit`** (ABC), construido desde campos ya parseados, **no** desde JSON crudo: `id`, `name`, `emoji`, `order` (pista para asignar teclas), `current_value` (progreso de hoy, **puede superar `goal`** — 10/8 es válido), `manual_entry` (default `False`, vive en la clase base para que `core.screens` lo lea sin `isinstance`); propiedades `goal` (default `1.0`) e `is_done` (`current_value >= goal`, que **solo decide el color de la tecla, no bloquea nada**); `display_label()` abstracto. `BooleanHabit` → muestra el nombre. `RealHabit` (cuantificables, con `goal`/`step`/`unit`) → muestra solo el progreso (`"3/8 Cups"`, o `"10/8 Cups"` por encima del objetivo, sin decimales feos en enteros), **salvo si `manual_entry` es `True`** (p.ej. "Peso"): ahí no hay "progreso hacia un objetivo" que enseñar — el valor es una medición, no un avance — así que se comporta como `BooleanHabit` y muestra el nombre, sin número; el valor se ve al pulsar, en el teclado numérico. Si `manual_entry` es `True`, pulsar la tecla tampoco suma `step`: abre el teclado numérico (ver [Menú y pantallas](#menú-y-pantallas)).
  - **`Task`** — concreta y sin subtipos, al revés que `Habit`: una tarea está pendiente o deja de existir, no tiene estado "hecha" que pintar. Campos: `id` (el de la **ocurrencia**, es lo que se envía para cerrarla), `title`, `emoji`, `priority` (solo `0`/`1`/`3`/`5`, no son contiguos), `overdue` (arrastra de un día anterior), `due_day`, `template_id` (vacío si es tarea única; lo usa "Crear" para saber qué plantillas ya tienen ocurrencia abierta). `display_label()` devuelve el título recortado a `TITLE_MAX_CHARS` con elipsis. **La base no guarda icono de tarea**, pero es habitual escribir el emoji dentro del título (`"Bano 🚽"`): `build_task` lo separa a `emoji` para pintarlo como icono a color, igual que el `icon_res` de un hábito. El color de la tecla lo sigue dando la prioridad.
  - **`Template`** — la definición reutilizable de una tarea que se repite **sin momento fijo** ("Cita peluquero"). Campos: `id` (el de la **plantilla**, no el de la tarea que se crea), `title`, `emoji` (mismo criterio que `Task`), `priority`, y `has_pending` — **mutable, y no viene del backend**: lo calcula `core.screens._create_items` en cada resolución cruzando plantillas con tareas pendientes. Al revés que una `Task`, una plantilla **no desaparece** al usarla.
- **`supabase.py`** — el **adaptador**; concentra todo lo específico del backend:
  - Lee `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` del `.env`; `__init__` lanza `ProviderAuthError` si falta alguna. Base PostgREST `<url>/rest/v1`, cabeceras `apikey` + `Authorization: Bearer`.
  - `get_habits`: **una sola petición** a `v_today_habits` (la vista ya filtra activos y trae el progreso), con `order=sort_order` porque la vista no ordena por sí sola.
  - `step`: `POST /rpc/habit_step` con `{"p_habit_id": habit.id}`. Atómico en la base (Boolean → salta a `goal`; Real → suma `step` sin tope). Devuelve el nuevo total.
  - `undo`: `POST /rpc/habit_undo`, mismo cuerpo y mismo tratamiento de respuesta que `step`. Solo lo dispara la vista "Hábitos" sobre un booleano ya hecho (ver [Menú y pantallas](#menú-y-pantallas)).
  - `set_value`: `POST /rpc/habit_set` con `{"p_habit_id": habit.id, "p_value": value}`. La base hace `greatest(p_value, 0)` y fija (no suma) el checkin de hoy; devuelve el nuevo total, mismo tratamiento de respuesta que `step`/`undo`. Solo lo dispara confirmar ("OK") en la pantalla de teclado numérico de un hábito `manual_entry`.
  - `get_tasks`: **una sola petición** a `v_today_tasks`, que ya excluye completadas y omitidas y arrastra las vencidas. Tampoco ordena por sí sola: el orden va explícito en `_TASKS_ORDER` (`priority.desc,due_date.asc`).
  - `complete_task`: `POST /rpc/complete_task` con `{"p_task_id": task.id}`. La función devuelve `void`, así que responde **204 sin cuerpo**: no hay JSON que parsear y el status es toda la confirmación. Es idempotente en la base, reintentar es seguro.
  - `skip_task`: `POST /rpc/skip_task` con `{"p_task_id": task.id}`. Misma forma que `complete_task` (`void`, 204 sin cuerpo, idempotente). RPC preexistente del contrato (ya usada por otros clientes), así que añadir esta opción al deck no tocó `habits-core`.
  - `set_priority`: `POST /rpc/set_task_priority` con `{"p_task_id": task.id, "p_priority": priority}`. Misma forma que `complete_task` (`void`, 204 sin cuerpo). La RPC solo toca ocurrencias pendientes; sobre una ya completada/omitida o inexistente no hace nada, ni falla.
  - `get_templates`: **una sola petición** a `v_templates` con `show_in_deck=eq.true`. La vista devuelve todas las plantillas activas (también las que materializará `pg_cron`, que aquí no se pintan) y **el filtro es del cliente a propósito**: así la PWA sigue viendo la lista completa con el mismo `grant`, sin necesidad de una vista nueva.
  - `create_task`: `POST /rpc/instantiate_task` con `{"p_template_id": template.id}`. **No manda fecha** — sin `p_due` la base hace vencer la ocurrencia ahora, que es lo que la hace aparecer en `v_today_tasks`; si mandara `null` explícito nacería invisible. Al contrario que `complete_task`, devuelve el `uuid` nuevo (200 **con** cuerpo JSON, hay que parsearlo), y **no es idempotente**: dos llamadas, dos tareas.
  - Traduce `RequestException` / 401-403 / no-2xx / JSON inválido a las excepciones `Provider*`. Un 401/403 puede ser clave inválida **o** un `GRANT` que falta en el contrato; el mensaje lo insinúa.
  - `build_habit(data)`: parsea `icon_res` (`"txt_<emoji>"` → emoji vía `core.emoji.extract_emoji`; los predefinidos tipo `"habit_water"` dan emoji vacío), enruta `type == "Real"` a `RealHabit` (con `manual_entry` de la columna homónima) y todo lo demás (incluido `"Boolean"` y valores desconocidos) a `BooleanHabit`, toma `order` de `sort_order`. `build_task(data)` es el equivalente para tareas, sin ramas: la vista solo devuelve pendientes.
  - Las tablas (`habits`, `habit_checkins`, `tasks`, …) están cerradas con RLS y no son accesibles con la clave publishable: este adaptador nunca las menciona. El contrato está en `../habits-core/docs/contrato.md`, el porqué de la regla en `../habits-core/docs/estructura-bd.md` y peticiones de ejemplo listas para lanzar en `../habits-core/tools/supabase.http`.

### `deck/` — hardware y pintado

- **`session.py`** — `DeckSession`: apertura (30 reintentos cada 2s; `sys.exit(1)` si no aparece), cierre, `reconnect()` y brillo (`set_brightness()`, entre `BRIGHTNESS = 60` y `BRIGHTNESS_STANDBY = 0`). `reconnect()` solo se usa tras un fallo en marcha, nunca en el arranque inicial, y **reabre siempre a `BRIGHTNESS`**: por eso el bucle principal re-aplica el brillo de stand by tras reconectar (ver [Stand by](#stand-by)).
- **`renderer.py`** — alto nivel, sobre una tecla o el deck completo: `render_habit` (usa `display_label()`, `emoji` e `is_done` para elegir blanco/pendiente o gris/hecho — solo color, sin bloquear nada), `render_task` (color de fondo según `priority`; una prioridad desconocida cae a la 0 en vez de fallar), `render_task_sending` (verde vivo + ✔, el acuse de recibo de la pulsación), `render_checkin_error`, `render_empty`, `render_menu_key` (tecla 0, fija en toda pantalla), `render_arrow` (teclas 5/10: el icono ◀️/▶️ se pinta siempre, activa o no la paginación — solo cambia el fondo, `COLOR_ARROW` si hay más de una página o `COLOR_NEUTRAL` si no), `render_nav_entry` (un botón de menú o de Sistema; el de "Apagar" usa el rojo de aviso `COLOR_SHUTDOWN`, el resto el azul genérico `COLOR_NAV`), `render_template` (una plantilla de "Crear": morada si se puede usar, gris apagado si ya tiene ocurrencia abierta — ahí el gris **sí** significa deshabilitada, al revés que en un hábito), `render_numeric_key` (un botón del teclado numérico de entrada manual — dígito/".", "Borrar", "OK", "Salir" o la "pantalla" con lo tecleado, ver [Menú y pantallas](#menú-y-pantallas)), `render_option_entry` (una tecla del menú de opciones de un hábito/tarea — "Volver" en azul de navegación, o un mensaje informativo, ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado)), `render_page` (pinta las 15 teclas a partir de un `core.screens.ResolvedPage` ya resuelto: si la pantalla activa es el teclado numérico o el menú de opciones, las 15 salen de `key_numeric`/`key_options` — incluidas 0/5/10, reinterpretadas ahí —, si no, menú fijo en 0, flecha en 5/10, entrada de menú/hábito/tarea/plantilla en el resto, vacía si no hay nada), `render_error_all` (pinta el código de error en un conjunto de teclas — las de hábitos o las de tareas de la página visible, según cuál haya fallado, para no dejar información obsoleta en pantalla).
- **`primitives.py`** — Pillow de bajo nivel vía `PILHelper`: `solid_tile` (color plano) y `text_tile` (texto envuelto/centrado más un `emoji` opcional como icono a color en la mitad superior, o la tecla entera si `text` es vacío). El emoji usa `_emoji_font()` (`NotoColorEmoji.ttf`, probando los tamaños de "strike" conocidos porque es una fuente CBDT/CBLC de mapa de bits) y `_emoji_glyph()` (`embedded_color=True`, reescalado).
- **`keys.py`** — un único efecto lateral: `shutdown_pi()`, invocado desde el dispatcher de `orchestrator.py` cuando se pulsa "Apagar" en el submenú Sistema. Ver [Menú y pantallas](#menú-y-pantallas).
- **`style.py`** — todos los colores (`COLOR_*`) y tamaños de fuente (`FONT_SIZE_*`). Es la capa de pintado: no sabe de hábitos ni del proveedor. Incluye `COLOR_TASK_BY_PRIORITY` / `COLOR_TEXT_TASK_BY_PRIORITY` (dicts indexados por las prioridades `0`/`1`/`3`/`5`), `COLOR_TASK_SENDING`, los de navegación (`COLOR_MENU`, `COLOR_NEUTRAL`, `COLOR_NAV`, `COLOR_ARROW`), los de plantilla (`COLOR_TEMPLATE`, un morado que no choca con ninguno de los otros — una tecla morada significa siempre "esto crea algo" — y `COLOR_TEMPLATE_PENDING`, que es literalmente `COLOR_HABIT_DONE`: mismo gris, mismo significado de "esto ya está") y los del teclado numérico (`COLOR_NUMERIC` para dígitos/".", `COLOR_NUMERIC_BACKSPACE` ámbar, `COLOR_CONFIRM` verde estático del "OK", `COLOR_NUMERIC_DISPLAY` para la "pantalla" del valor tecleado; "Salir" reutiliza `COLOR_NAV`, mismo rol que la tecla de menú). El menú de opciones de un hábito/tarea reutiliza `COLOR_NAV` para "Volver" (mismo rol) y añade `COLOR_OPTIONS_MESSAGE`, un gris azulado propio y apagado para el mensaje informativo, deliberadamente distinto de cualquier tecla de contenido pulsable. Ojo: el rojo de prioridad 5 es **distinto** de `COLOR_ERROR` a propósito, para que una tarea urgente no se confunda con una tecla en error; una tarea de prioridad 0 es blanca, igual que un hábito pendiente; y `COLOR_NUMERIC_BACKSPACE` es igualmente distinto de `COLOR_ERROR`, para que "Borrar" no parezca un fallo.

### `core/` — dominio agnóstico

- **`screens.py`** — el registro de menú/pantallas, ver [Menú y pantallas](#menú-y-pantallas).
- **`key_map.py`** — el mapeo persistido de hábitos, más la paginación genérica:
  - `update_mapping` persiste el mapeo hábito→tecla en `habit_key_map.json`. Recibe `list[Habit]`, no JSON. Los hábitos nuevos reclaman la tecla libre más baja en orden `(order, id)` y **nunca se reasignan**; los que desaparecen liberan su tecla; sin teclas libres se registra `KFUL` y se omite. **Solo debe llamarse tras un `get_habits()` exitoso** — llamarlo tras un fallo liberaría las teclas de hábitos que siguen existiendo. Solo lo consulta la vista "Hábitos" (ver [Reparto de teclas](#reparto-de-teclas)).
  - `paginate(items, page, page_size)` recorta una lista a una página (saturada a rango válido, mínimo 1 página). La usa `core.screens` para menú, Sistema, el sobrante de "Hábitos" que no quepa en una sola página, y para paginar "Hoy"/"Tareas" enteras (que no reservan tecla, se recalculan de cero cada vez).
- **`health.py`** — `classify(exc)` decide el destino de un fallo: `"key"` (excepciones `Provider*` → `AUTH`/`NET`/`API`, se pintan en rojo) o `"file"` (fallos del propio Stream Deck, solo a fichero, sin código). `log_failure(item_id, detail, kind)` → `checkin_failures.log` (JSON-lines; `kind` es `"habit"` o `"task"`); `log_device_error` → `device_errors.log` (texto plano).
- **`error_codes.py`** — `CODES`: los cuatro códigos cortos que caben en una tecla.
- **`emoji.py`** — `extract_emoji(text) -> (emoji, resto)`, cubriendo variation selectors y ZWJ. Sin dependencias de Pillow ni de ningún proveedor, para poder usarse desde cualquier adaptador.

## Menú y pantallas

La tecla 0 (`config.KEY_MENU`) abre siempre el menú principal, desde cualquier pantalla (no hace nada si ya estás en el menú). El menú tiene botones "Hoy", "Hábitos", "Tareas", "Crear" y "Sistema"; "Sistema" es un submenú con "Suspender" y "Apagar" — sin botón "Atrás": sería redundante, la tecla 0 ya vuelve al menú principal desde Sistema igual que desde cualquier otra pantalla. La vista por defecto, tanto al arrancar el daemon como al volver por inactividad o del stand by, es "Hoy" (`core.screens.DEFAULT_VIEW_ID`).

Todo esto se resuelve con una sola abstracción en `core/screens.py`: una pantalla activa (`ScreenState`: menú, Sistema o una vista con su página) se resuelve contra los datos vigentes con `resolve_page(...)` a un `ResolvedPage` (qué hábito/tarea/plantilla/entrada de menú pintar en cada tecla, más el total de páginas), y una pulsación se resuelve con `resolve_press(screen, key, page)` a una `PressAction` que el dispatcher de `orchestrator.py` ejecuta. `deck/renderer.py::render_page` pinta cualquier `ResolvedPage` con el mismo bucle, sea menú, Sistema o una vista.

**Añadir una vista nueva** (p.ej. "por proyecto") es: una función `items_fn(habits, tasks, templates)` que decide qué le toca, una entrada nueva en `core.screens.VIEWS` (usando `_flat_page_builder` si no necesita reutilizar el mapeo estable de hábitos, o `_tiered_page_builder` si sí) y un botón más en `MENU_ENTRIES`. Todo builder recibe los **tres** conjuntos de datos aunque casi ninguno los use enteros: es lo que permite cruzarlos (así "Crear" sabe qué plantillas ya tienen ocurrencia abierta). Nada más del sistema cambia — incluido el deshacer, que es opt-in por vista (`ViewSpec.allows_undo`, ver abajo). La única excepción está fuera de `core/screens.py`: si la vista debe mostrar códigos de error, hay que añadir su `view_id` a las listas literales de `orchestrator._paint_current_screen`.

Las teclas 5 y 10 (`KEY_PAGE_PREV`/`KEY_PAGE_NEXT`) muestran siempre el icono de flecha "◀"/"▶", para que se reconozcan de un vistazo igual que la de menú; el fondo distingue si hacen algo: activo (`COLOR_ARROW`) cuando la pantalla activa (menú, Sistema o una vista) tiene más de `PAGE_SIZE` (12) ítems, neutro (`COLOR_NEUTRAL`) si no. `PAGE_SIZE` es una única constante para todas las pantallas: 5 y 10 nunca han sido teclas de contenido, con o sin más de una página caben 12 ítems por página.

**El teclado numérico es la única pantalla que reinterpreta las teclas reservadas 0/5/10.** Lo abre pulsar, desde "Hoy" o "Hábitos", un hábito cuantificable con `manual_entry = true` en la base (p.ej. "Peso": no tiene sentido ir sumando `step` de 1 en 1, hay que fijar el valor exacto del día). `ScreenState` gana `ScreenKind.NUMERIC_ENTRY` y dos campos, `entry_habit_id`/`entry_value`; al entrar **no se tocan** `view_id`/`page`, así que "Salir" vuelve exactamente a la vista de origen sin necesitar un campo "volver a" aparte. Layout fijo (`core.screens.NUMERIC_KEYPAD`), numeración de fila (5 columnas × 3 filas):

```
Salir    valor   1   2   3
Borrar     0     4   5   6
  OK       .     7   8   9
```

Aquí la tecla 0 es "Salir" (no abre el menú principal), la 5 es "Borrar" (no la flecha "◀") y la 10 es "OK" (no la flecha "▶") — `core.screens.resolve_press` comprueba `ScreenKind.NUMERIC_ENTRY` **antes** que `KEY_MENU`/`KEY_PAGE_PREV`/`KEY_PAGE_NEXT` a propósito, para no arrastrar su significado habitual. Teclear un dígito, el punto o borrar solo muta `entry_value` y repinta (sin red); "OK" con el campo vacío o inválido (p.ej. solo ".") no hace nada, para poder seguir tecleando. Confirmar llama a `HabitProvider.set_value` (`rpc/habit_set`, ver arriba): con éxito, mutación optimista y vuelta a la vista de origen; con fallo, la tecla "OK" queda en rojo con el código y la pantalla se queda en el teclado con lo tecleado intacto, para reintentar sin perder nada (mismo patrón de acuse/fallo que un hábito o una tarea).

Un hábito `manual_entry` **no tiene "deshacer"**: a diferencia de "Hábitos" con un booleano ya hecho, pulsarlo siempre abre el teclado, esté hecho hoy o no — `habit_set` sobrescribe el checkin de hoy, así que re-teclear ya es la corrección. No hay entrada nueva en el menú principal: la única forma de llegar aquí es pulsando un hábito `manual_entry` en "Hoy" o "Hábitos".

Entrar en una vista desde el menú ("Hoy"/"Hábitos"/"Tareas"/"Crear") dispara un refresco completo (refetch + repintado) antes de pintarla; navegar dentro de una pantalla ya cargada (menú, Sistema, cambiar de página) solo repinta, sin refetch. El refresco periódico de `REFRESH_SECONDS` sigue actualizando los datos en segundo plano y repinta lo que esté visible en cada momento, sea cual sea la pantalla activa.

Si el deck lleva `AUTO_RETURN_SECONDS` (5 min) sin ninguna pulsación estando fuera de "Hoy", vuelve solo a "Hoy" (con los datos ya en caché, sin refetch). Cualquier pulsación, en cualquier pantalla, reprograma ese temporizador — y también el de stand by, ver abajo.

### Menú de opciones de un hábito/tarea (mantener pulsado)

El menú de un **hábito** sigue siendo un **prototipo**: solo un par de teclas de aviso ("Opciones de hábito" + "Próximamente"), la maqueta sobre la que montar opciones de verdad (editar, posponer…) en una sesión futura. El de una **tarea** ya tiene dos opciones reales: **Skip** (tecla 1) y **cambiar la prioridad** (teclas 11-14). Ver "Cómo extenderlo" más abajo para añadir opciones nuevas a cualquiera de los dos.

Mantener pulsado un hábito o una tarea (en cualquier vista que los muestre: "Hoy", "Hábitos", "Tareas") en vez de soltarlo enseguida abre `ScreenKind.ITEM_OPTIONS`. **Esto cambia cuándo se ejecuta la acción normal de un hábito/tarea**: ya no es al presionar, es al **soltar** (ver `orchestrator.make_key_callback`):

- Al presionar una tecla que resuelve a `"habit"`/`"habit_undo"`/`"task"`, se arma un temporizador de `config.LONG_PRESS_SECONDS` (0.6 s) en vez de actuar.
- Si se suelta **antes**, se cancela el temporizador y se ejecuta la acción de siempre (paso, deshacer o cierre) — con la latencia típica de un toque humano, imperceptible.
- Si el temporizador **dispara con la tecla todavía pulsada**, abre el menú de opciones para ese hábito/tarea; la liberación posterior de la tecla no hace nada, ya quedó consumida.

Las demás teclas (plantilla, navegación, teclado numérico) no tienen esta espera: siguen actuando al presionar, sin cambios.

`ScreenState` gana `ScreenKind.ITEM_OPTIONS` y dos campos, `entry_item_kind` ("habit"/"task") y `entry_item_id`; igual que `NUMERIC_ENTRY`, entrar **no toca** `view_id`/`page`, así que "Volver" regresa exactamente a la vista/página de origen sin necesitar un campo aparte. **Un hábito y una tarea llevan a pantallas distintas**: `core.screens.resolve_page` elige `HABIT_OPTIONS_LAYOUT` o `TASK_OPTIONS_LAYOUT` según `entry_item_kind` — dos layouts fijos separados a propósito, porque sus opciones futuras serán distintas (una tarea no tiene objetivo ni "deshacer", un hábito no tiene fecha de vencimiento). En ambos: tecla 0 = "Volver" (mismo azul de navegación que "Salir" en el teclado numérico — mismo rol: esto te saca de aquí sin tocar nada). `HABIT_OPTIONS_LAYOUT` solo añade teclas 6/7 de mensaje informativo, el resto vacías; `TASK_OPTIONS_LAYOUT` añade la tecla 1 ("Skip", naranja), la tecla 5 como cabecera ("Prioridad") y las teclas 11-14 con los cuatro colores de prioridad (ver más abajo). Las teclas 0/5/10 **no** se reinterpretan aquí como en el teclado numérico: `resolve_press` resuelve `ITEM_OPTIONS` antes de llegar a esa lógica, así que la tecla 5 puede llevar contenido normal como cualquier otra. `core.screens.resolve_press` comprueba `ScreenKind.ITEM_OPTIONS` **antes** que `KEY_MENU`/paginación, mismo motivo que `NUMERIC_ENTRY`: aquí la tecla 0 no abre el menú principal.

Un hábito `manual_entry` (que ya reinterpreta su propia pulsación para abrir el teclado numérico) queda **fuera** de este mecanismo: `resolve_press` lo resuelve a `"habit_enter_value"`, no a `"habit"`/`"habit_undo"`, así que mantenerlo pulsado no hace nada especial — sigue abriendo el teclado al instante, como siempre.

**Cambiar la prioridad de una tarea** (`OptionEntry.kind == "priority"`, teclas 11-14 de `TASK_OPTIONS_LAYOUT`, blanco/verde/amarillo/rojo — los mismos colores que `deck.style.COLOR_TASK_BY_PRIORITY`): pulsar una de estas cuatro teclas resuelve a `PressAction("task_set_priority", payload=<prioridad como texto>)`. El id de la tarea **no** va en el payload: `orchestrator._run_action` lo lee de `screen.entry_item_id` en el momento de ejecutar (mismo patrón que `"numeric_confirm"` con `entry_habit_id`/`entry_value`), lo reserva con `_claim`/`_release` como cualquier otra escritura, y llama a `TaskProvider.set_priority` (`rpc/set_task_priority` en `../habits-core`, que solo toca ocurrencias pendientes). Éxito → mutación optimista de `task.priority` + `exit_item_options` (vuelve a la vista de origen, ya con el color nuevo si esa tarea sigue visible ahí). Fallo → la tecla pulsada queda en rojo con el código, sin salir del menú, para poder reintentar sin perder el contexto — mismo patrón de acuse que un paso de hábito o un cierre de tarea.

**Omitir (skip) una tarea** (`OptionEntry.kind == "skip"`, tecla 1 de `TASK_OPTIONS_LAYOUT`, naranja propio `deck.style.COLOR_TASK_SKIP`): resuelve a `PressAction("task_skip")`, sin payload — el id de la tarea sale de `screen.entry_item_id`, igual que el cambio de prioridad. Pulsarla pinta primero el acuse verde de siempre (`render_task_sending`) y llama a `TaskProvider.skip_task` (`rpc/skip_task` en `../habits-core` — **RPC ya existente y ya con `grant` a `anon`**, así que esta opción fue enteramente cliente, sin tocar la base). Éxito → la tarea sale de `tasks_ref` (igual que al completarla) + `exit_item_options`: la vista de origen se repinta ya sin ella. Fallo → mismo patrón de tecla en rojo que el resto.

**Cómo extenderlo**: añadir una opción real es añadir una entrada a `HABIT_OPTIONS_LAYOUT` o `TASK_OPTIONS_LAYOUT` (según a cuál corresponda) con su propio `kind`, darle significado en `core.screens.resolve_press` (un `PressAction.kind` nuevo) y ejecutarla en `orchestrator._dispatch_navigation` si es pura navegación, o con el mismo patrón `_claim`/`_release` + lectura de `screen.entry_item_id` que usan `"task_set_priority"`/`"task_skip"` si toca red — mismo patrón que cualquier otro layout fijo de este módulo (`NUMERIC_KEYPAD`, `STANDBY_LAYOUT`). El elemento sobre el que actuar ya está disponible en `screen.entry_item_kind`/`entry_item_id`.

## Stand by

`ScreenKind.STANDBY` es la quinta pantalla: baja la retroiluminación del deck al mínimo (`session.set_brightness(BRIGHTNESS_STANDBY)`), pinta una pantalla fija con un icono y suspende el ciclo de refresco. Entra de dos formas, ambas por `orchestrator._enter_standby`:

- **Sola**, tras `config.STANDBY_SECONDS` (30 min) sin ninguna pulsación. Es un segundo `_IdleTimer` con el mismo disparador que el de auto-retorno (cualquier tecla los reprograma los dos) y plazo distinto, así que lo normal es que primero vuelva a "Hoy" a los 5 min y 25 min después se apague.
- **A mano**, con el botón "Suspender" del submenú Sistema.

**Es la única palanca de consumo real que tiene el daemon, y es la que importa.** La deck MK.2 declara `MaxPower 500mA` y consume ~300 mA (~1,5 W) en marcha; los 15 backlights son el grueso, así que bajarlos al mínimo ahorra del orden de ~0,7–0,9 W de los ~2,5–3 W del conjunto Pi+deck (a brillo 0 serían ~0,1 W más, que es lo que cuesta ver el icono). Todo lo demás que se probó en la Pi 3 no compensa: `ondemand` ya baja sola a 600 MHz en reposo, el daemon ya consume 0,2% de CPU (bajar `set_poll_frequency` no da nada), y HDMI/LEDs/`wlan0` suman ~0,1–0,3 W a cambio de reglas `sudoers` nuevas. Cortar la alimentación USB o dejar que la deck haga autosuspend USB mataría el propio camino de despertado. **No se puede medir por software**: la Pi 3 no tiene el ADC del PMIC (`vcgencmd pmic_read_adc` responde `Command not registered`, es de Pi 4/5); haría falta un medidor USB intercalado.

**Pulsar cualquier tecla despierta, y esa pulsación no hace nada más.** `core.screens.resolve_press` comprueba `ScreenKind.STANDBY` **lo primero de todo** — antes que `NUMERIC_ENTRY` y antes que `KEY_MENU`/paginación — y devuelve `"wake"` sin llegar a mirar el índice de tecla. Es lo que garantiza que encender el deck a ciegas no cierre la tarea ni marque el hábito que hubiera debajo. Cualquier pantalla nueva que se añada debe respetar ese orden.

Despertar (`orchestrator._wake`) es **datos frescos primero, luz después**: reutiliza `_enter_view(DEFAULT_VIEW_ID)`, que ya reserva el centinela de navegación (un doble toque no dispara dos refrescos), saca de `STANDBY` y fuerza un `refresh_cycle()` completo. Como ese repintado ocurre con el brillo todavía a 0, el deck se enciende ya con el contenido correcto: sin destello de datos viejos ni doble repintado. A cambio, tarda lo que tarde la red (1-2 s). El `finally` que sube el brillo **no es decorativo**: si el proveedor está caído o falla el dispositivo, el deck tiene que encenderse igual o se quedaría negro para siempre.

### Qué se ve mientras está suspendido

El brillo de stand by **no es 0** a propósito: a 0 el deck parece apagado o colgado. `BRIGHTNESS_STANDBY = 10` (en `deck/session.py`) deja lo justo para que se distinga un icono, y es la constante a tocar para ajustarlo a ojo. Ojo: **el brillo del Stream Deck es global, no hay control por tecla**, así que ese valor ilumina tenuemente las 15 y subirlo se come parte del ahorro casi en proporción directa.

**Lo que se ve sale entero de `core.screens.STANDBY_LAYOUT`, y ese dict es el único sitio que hay que tocar para cambiarlo**:

```python
STANDBY_LAYOUT: dict[int, StandbyKey] = {
    7: StandbyKey("", "🌙"),   # tecla central: solo icono, sin texto
}
```

Las teclas que no aparecen se pintan negras. Añadir, quitar o mover contenido es editar entradas de ese dict — nada más del sistema cambia: `resolve_page` lo expande a las 15 teclas (rellenando con `_STANDBY_BLANK`) y `deck.renderer.render_standby_key` pinta cada una. Los colores y el tamaño de fuente están en `deck/style.py` (`COLOR_STANDBY`, `COLOR_TEXT_STANDBY`, `FONT_SIZE_STANDBY`). `StandbyKey` tiene `label` y `emoji`, igual que un botón de menú, así que una tecla puede llevar texto, icono o ambos.

Que se resuelva a las 15 teclas (y no solo a las que tienen contenido) es lo que hace que `render_page` la trate como un bloque, igual que el teclado numérico: si no, las teclas 0/5/10 recaerían en menú/paginación, que aquí no significan nada.

### Tres detalles que no son obvios y conviene no "simplificar"

- **Entrar en stand by pinta de verdad, y baja el brillo antes de pintar.** Pintar es necesario porque con brillo > 0 la pantalla anterior se seguiría intuyendo. Y el orden (brillo primero) hace que la transición se lea como un fundido en vez de como un parpadeo de pantalla nueva a plena luz.
- **`_on_auto_return_timeout` sale antes si está suspendido.** Con el botón "Suspender" el stand by se adelanta pero el temporizador de auto-retorno sigue armado; sin esa salida, a los 5 min sacaría de `STANDBY` sin encender la pantalla y dejaría el deck a oscuras con las teclas otra vez activas — justo lo que `"wake"` existe para impedir.
- **`_enter_standby` comprueba si ya está suspendido.** No es solo defensivo: pulsar "Suspender" reprograma el temporizador de stand by (`on_key_change` lo hace en toda pulsación), que volverá a disparar estando ya suspendido.

**"Hoy" es la única vista que se "vacía", y sin dejar hueco.** Es la única de las tres que usa `_flat_page_builder` en vez del mapeo estable de hábitos: `core.screens._today_items` construye, en cada resolución, la lista de hábitos aún no completados hoy (`not habit.is_done`, ordenados por `(order, id)`) seguida de las tareas pendientes (ya vienen ordenadas por prioridad/fecha), y esa lista se reparte de cero desde la primera tecla disponible con `key_map.paginate` — sin reservar nada. Al completar un hábito o cerrar una tarea, `make_key_callback` repinta la **pantalla entera** (no solo esa tecla): el item desaparece de la lista y todo lo que queda se recoloca una posición, sin dejar hueco entre botones activos. "Hábitos" **no** hace esto a propósito: sigue usando `_tiered_page_builder` (mapeo estable, ver [Reparto de teclas](#reparto-de-teclas)) y muestra el estado de todos, hechos o no, para poder repasarlos — ahí nada desaparece, así que no hay huecos que evitar.

**"Hábitos" es además la vista que deshace.** Como es la única que enseña los hábitos ya hechos (en gris), es la que sirve para corregir una pulsación errónea: pulsar ahí un hábito **booleano** ya marcado hoy llama a `habit_undo` en vez de a `habit_step`, el checkin del día vuelve a 0 y la tecla se repinta en blanco (pendiente). Volver a pulsarla lo marca otra vez: el ciclo es reversible sin límite. Un hábito **cuantificable** no deshace nunca al pulsarlo — sigue sumando `step` aunque ya haya pasado el objetivo (`10/8` → `11/8`), que es justo lo que su tecla en gris significa. La decisión es pura y vive en `core/screens.py`: `ViewSpec.allows_undo` (solo `True` en "Hábitos"; una vista nueva no lo hereda) más el tipo y el estado del hábito hacen que `resolve_press` devuelva una `PressAction` de tipo `"habit_undo"` en vez de `"habit"`. En "Hoy" no aplica: los hábitos hechos ni siquiera aparecen.

**"Crear" es la única vista que escribe algo nuevo en vez de modificar lo que ya hay.** Lista las plantillas de `task_templates` marcadas con `show_in_deck` en la base — las tareas que se repiten **sin momento fijo** ("Cita peluquero"), que ni materializa `pg_cron` ni encadena `complete_task` —, y al pulsar una crea la ocurrencia con `instantiate_task`. La tarea nueva vence hoy (lo decide la base, el daemon no manda fecha) y aparece en "Hoy" y "Tareas" al instante.

Qué plantillas salen aquí **no se configura en el daemon**: es la columna `show_in_deck` de `../habits-core`, opt-in (`default false`). Añadir un botón nuevo es un `update`, no un despliegue.

Dos detalles que la distinguen de las demás:

- **La plantilla no desaparece al usarla**, al revés que una tarea al cerrarse: se reutiliza. Lo que cambia es su color.
- **`instantiate_task` no es idempotente** (dos pulsaciones, dos tareas), así que una plantilla que ya tiene una ocurrencia pendiente se pinta en **gris y su tecla no hace nada**. Es la única tecla de contenido del deck que se bloquea de verdad — en todo lo demás la regla es "la tecla nunca se bloquea, decide la base", pero aquí la base no puede decidir nada: no hay forma de distinguir un duplicado accidental de uno querido. Toda la lógica está en `core.screens._create_items`, que cruza las plantillas con `Task.template_id` y marca `Template.has_pending`; `resolve_press` lo convierte en `noop` y `render_template` en gris.

Ese cruce se **recalcula en cada resolución**, no se acumula: si la ocurrencia se cierra, la plantilla vuelve a estar disponible sola. Por eso `press_template`, al crear con éxito, inserta la tarea nueva en `tasks_ref` en vez de tocar `has_pending` a mano — marcar el flag se perdería en el primer repintado. Es tan reciente como el último ciclo: una ocurrencia creada desde otro cliente hace un minuto todavía no se ve, y se asume — el gris es una red, no un candado.

## El bucle principal

`main()` construye el proveedor (`sys.exit(1)` si falla), abre la sesión del deck, carga el mapeo y entra en un bucle infinito de `refresh_cycle()` separados por `time.sleep(REFRESH_SECONDS)` (900s). **En stand by el ciclo se salta entero** (`if not _is_standby()`): no tiene sentido pedir datos ni repintar una pantalla apagada, y el despertado ya fuerza su propio ciclo completo. El bucle sigue despertando cada 15 min sin hacer nada; no compensa complicarlo. El mismo `SupabaseProvider` se usa por sus tres puertos (`habit_provider` / `task_provider` / `template_provider`), solo para dejar explícito qué capacidad usa cada llamada. Todo el estado de pantalla (`screen: core.screens.ScreenState`, `mapping`) está serializado por un `screen_lock` que comparten `refresh_cycle`, las funciones de navegación y el callback de tecla (incluido `repaint()` tras un paso/cierre con éxito), para que un ciclo periódico y una pulsación del usuario nunca se entrelacen.

**Cada ciclo (`refresh_cycle`, bajo `screen_lock`):**

1. `get_habits()` — una sola petición; ya trae el progreso de hoy en `current_value`, no hay ninguna fecha que decidir en el cliente. Éxito → `key_map.update_mapping()` y se limpia el código de error de hábitos; fallo → se guarda el código en `last_habits_code` y se conservan mapeo y datos del ciclo anterior.
2. `get_tasks()` — otra sola petición; solo pendientes y ya ordenadas. Éxito → se guardan en `tasks_ref` y se limpia el código de error de tareas; mismo tratamiento de fallo que arriba con `last_tasks_code`. Las tareas no tienen mapeo propio: cada vista las reparte en tecla cuando le toca pintarse (ver [Reparto de teclas](#reparto-de-teclas)).
3. `get_templates()` — la tercera; solo las de creación rápida, ya ordenadas por título. Éxito → `templates_ref` y se limpia `last_templates_code`; mismo tratamiento de fallo. Cambian rarísimamente, pero se releen cada ciclo por simetría: una petición cada 15 min no compensa un camino especial.
4. `_paint_current_screen()`: resuelve la **pantalla activa** (no solo "Hoy": lo que sea que esté abierto — menú, Sistema o cualquier vista) contra los datos que acaban de llegar (`core.screens.resolve_page`), la pinta (`renderer.render_page`), pinta encima los códigos de error guardados **si la pantalla visible los usa** (`last_habits_code` solo en "Hoy"/"Hábitos", `last_tasks_code` en "Hoy"/"Tareas", `last_templates_code` solo en "Crear"), y **re-registra** `deck.set_key_callback(...)` con un closure fresco.

**Las tres lecturas fallan por separado**: la que falla no toca su mapeo ni sus datos (se conservan los del ciclo anterior) y no afecta a las otras. El ciclo **siempre** llega a pintar y a re-registrar el callback, de modo que lo pintado y lo que hace cada tecla nunca se desincronizan.

**Reactivación de un proyecto Supabase pausado.** Un proyecto del plan gratuito se pausa solo tras ~1 semana sin tráfico: su subdominio de API deja de resolver por DNS, lo que `provider.supabase` traduce en el mismo `ProviderNetworkError`/`NET` que "la Pi no tiene red" — son indistinguibles desde el propio checkin. Al final de `refresh_cycle` (ya **fuera** de `screen_lock`, es una llamada de red que no toca pantalla ni mapeo), si cualquiera de las tres lecturas trajo `NET`, se lanza en su propio hilo `provider.keepalive.try_restore_active_project()`, que pide la reactivación a la Management API de Supabase (`api.supabase.com`, no PostgREST). Es una **credencial y una API distintas** de las que usa `provider/supabase.py`: requiere `SUPABASE_ACCESS_TOKEN` (Personal Access Token de la cuenta, no la clave publishable) en el `.env` — sin él, la función no hace nada y el daemon se comporta exactamente igual que antes de que este módulo existiera. `config.RESTORE_COOLDOWN_SECONDS` (30 min) evita repetir la petición mientras el proyecto sigue "despertando" (tarda uno o dos minutos; se confirma solo, en un ciclo posterior).

El estado que ve el callback son wrappers dict de una entrada (`habits_ref`/`tasks_ref`/`templates_ref = {"value": {id: obj}}`) precisamente para que el closure observe las actualizaciones de ciclos posteriores. Aun así, **el mapeo tecla→elemento solo es tan reciente como el último ciclo o la última navegación.**

**Al pulsar una tecla (`make_key_callback`):**

1. Reprograma **los dos** temporizadores de inactividad (`AUTO_RETURN_SECONDS` y `STANDBY_SECONDS`, vía `_reset_idle_timers`), sea cual sea la tecla. Si la pantalla activa es el stand by, la pulsación no hace nada más que despertar (ver [Stand by](#stand-by)).
2. Bajo `screen_lock`, resuelve la página activa (`core.screens.resolve_page`) y la pulsación contra ella (`core.screens.resolve_press`) a una `PressAction`.
3. Si la acción es de hábito o tarea (`"habit"`/`"habit_undo"`/`"task"`), **no se ejecuta todavía**: se arma un temporizador de `LONG_PRESS_SECONDS`. Si la tecla se suelta antes, se cancela y se ejecuta como una pulsación corta (pasos 4/5 de abajo); si el temporizador dispara con la tecla aún pulsada, se abre el menú de opciones del elemento en su lugar (ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado)) y la liberación posterior de la tecla no hace nada. El resto de acciones (plantilla, navegación, teclado numérico) se ejecuta al presionar, sin esta espera.
4. **Hábito** (pulsación corta) → `provider.step(habit)`, **sin comprobar si ya alcanzó el objetivo**: la tecla nunca se bloquea, es la base quien decide el nuevo valor. Éxito → `habit.current_value = new_value` (mutación directa del objeto compartido con `habits_ref`) y `repaint()`: repinta la **pantalla activa entera**, optimista, sin refetch — no basta con esa tecla porque en "Hoy" el hábito puede desaparecer de la lista y hacer que el resto se recoloque (ver [Menú y pantallas](#menú-y-pantallas)). `pending_requests` + `state_lock` (vía `_claim`/`_release`) descartan una segunda pulsación del mismo elemento mientras hay una en vuelo.

   **Deshacer un hábito** (`PressAction` de tipo `"habit_undo"`: booleano ya hecho en "Hábitos", ver [Menú y pantallas](#menú-y-pantallas), también pulsación corta) → `provider.undo(habit)`. Éxito → la misma mutación optimista **y además un `refresh_cycle()` completo** en vez del `repaint()` optimista: el valor que devuelve `habit_undo` es el del día, y en un hábito `weekly_quota` la vista pinta el contador de la semana, así que lo único fiable es releer. La mutación optimista se hace igualmente para que, si esa relectura falla, la tecla quede con el estado nuevo y no con el viejo. La reserva de `_claim` es el `habit_id`, así que un paso y un deshacer del mismo hábito tampoco pueden solaparse.
5. **Tarea** (pulsación corta) → primero `render_task_sending` (verde vivo + ✔, acuse de recibo inmediato), luego `complete_task(task)`. Solo cuando la base **confirma** (204), la tarea sale de `tasks_ref` y se llama a `repaint()`, por el mismo motivo que un hábito: en cualquier vista que la mostrara, lo que quede se recoloca sin dejar hueco, de inmediato, no en el siguiente ciclo.
6. **Plantilla** (solo en "Crear") → mismo acuse verde, luego `create_task(template)`. Al confirmar la base, la ocurrencia devuelta se **añade** a `tasks_ref` y se repinta: la plantilla se queda en pantalla (ahora en gris, y su tecla ya no hace nada) y la tarea nueva aparece en "Hoy"/"Tareas" sin esperar al siguiente ciclo. Se queda en "Crear", no navega: así se pueden encadenar varias.
7. **Cambiar la prioridad o hacer skip de una tarea** (dentro del menú de opciones de una tarea, `PressAction` de tipo `"task_set_priority"`/`"task_skip"`) → ninguna lleva el id de la tarea en el payload: se lee de `screen.entry_item_id` en el momento de ejecutar, se reserva con `_claim`/`_release` igual que cualquier otra escritura. `"task_set_priority"` llama a `set_priority(task, priority)`, éxito → mutación optimista de `task.priority` + `exit_item_options`. `"task_skip"` pinta primero el acuse verde (`render_task_sending`) y llama a `skip_task(task)`, éxito → la tarea sale de `tasks_ref` (igual que al completarla) + `exit_item_options`. Fallo en cualquiera de las dos → tecla en rojo con el código, sin salir del menú. Ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado).
8. **Navegación** (abrir menú/Sistema, volver, elegir vista, paginar, abrir/cerrar el menú de opciones, apagar) → se delega en el dispatcher de `orchestrator.py` (`_dispatch_navigation`), que llama a `_enter_menu`/`_enter_system`/`_enter_view`/`_change_page`/`_exit_item_options`/`deck_keys.shutdown_pi`. `_enter_view` (entrar en una vista desde el menú) usa el mismo patrón `_claim`/`_release` que un hábito/tarea/plantilla, con una clave centinela fija, para no disparar dos refrescos por un doble toque.
9. `ProviderError` (en un checkin/cierre/creación/cambio de prioridad) → tecla en rojo con el código + entrada en `checkin_failures.log`.
10. Cualquier otra excepción → se trata como fallo de dispositivo: **nunca se muestra en tecla**, se registra en `device_errors.log` y dispara `session.reconnect()`. Los repintados van envueltos en `_safe_render` justo por esto. Como `reconnect()` reabre el deck a `BRIGHTNESS`, el bucle re-aplica `BRIGHTNESS_STANDBY` después si estaba suspendido: si no, un fallo de dispositivo encendería el deck sin que nadie lo haya pulsado.

No hay tecla de refresco manual: entrar en una vista desde el menú ya fuerza un refresco completo (ver [Menú y pantallas](#menú-y-pantallas)).

## Reparto de teclas

De las 15 teclas, `RESERVED_KEYS = {0, 5, 10}` nunca llevan hábito, tarea, plantilla ni entrada de menú — solo cambia *qué* se pinta ahí según la pantalla (ver [Menú y pantallas](#menú-y-pantallas)); quedan 12 (`AVAILABLE_KEYS`, ver `config.PAGE_SIZE`) que son el único espacio de contenido, en menú, en Sistema y en cualquier vista por igual.

Dentro de "Hábitos", que reutiliza el mapeo estable de hábitos (`core.screens._tiered_page_builder`), la página 0 sigue las mismas reglas de siempre:

- **Los hábitos van primero** y su tecla es estable: se persiste en `habit_key_map.json` y no se reasigna nunca mientras el hábito exista.
- Lo que no cabe en la página 0 (antes se descartaba como `KFUL`) ya no se pierde: pasa a la página 1, 2... en el mismo orden, sin garantía de estabilidad entre ciclos ahí — es la red de seguridad, no el camino principal. Ver [Menú y pantallas](#menú-y-pantallas).

"Hoy", "Tareas" y "Crear" no reservan nada para hábitos ni persisten tecla: paginan de cero, cada vez, la lista completa que les toque desde la tecla 1 en adelante (`core.screens._flat_page_builder`) — en "Hoy" primero los hábitos aún no completados (por `order`), luego las tareas pendientes (por prioridad/fecha); en "Tareas", solo las tareas; en "Crear", solo las plantillas (por título). Por eso ninguna deja hueco al completar algo: todo se recalcula y recoloca desde el principio.

Con los 5 hábitos actuales eso deja los hábitos en las teclas 1-4 y 6 dentro de "Hábitos"; en "Hoy" las teclas dependen de cuántos hábitos quedan pendientes en cada momento, sin posición fija.

Las tareas se pintan con el color de su prioridad (`deck/style.py`): **0 blanca, 1 verde, 3 amarilla, 5 roja**. No llevan icono, así que una tarea de prioridad 0 se ve igual que un hábito booleano pendiente — es una consecuencia asumida del esquema de color elegido, no un descuido.

## Teclas reservadas

| Tecla | Constante | Acción |
|---|---|---|
| 0 | `KEY_MENU` | Abre el menú principal desde cualquier pantalla (no-op si ya está abierto) |
| 5 | `KEY_PAGE_PREV` | Flecha "◀" si la pantalla activa tiene más de una página; gris neutro si no |
| 10 | `KEY_PAGE_NEXT` | Flecha "▶" si la pantalla activa tiene más de una página; gris neutro si no |

En el teclado numérico las tres se reinterpretan (salir/borrar/OK); en el menú de opciones de un hábito/tarea (mantener pulsado) la tecla 0 se reinterpreta como "Volver" y la 5/10 quedan vacías (ver [Menú de opciones de un hábito/tarea (mantener pulsado)](#menú-de-opciones-de-un-hábitotarea-mantener-pulsado)); y **en stand by ninguna hace lo de siempre**: cualquiera de las 15 se limita a despertar el deck (ver [Stand by](#stand-by)).

El apagado de la Pi ya no es una tecla fija: es el botón "Apagar" del submenú Sistema (menú → Sistema → Apagar), pintado en rojo de aviso para seguir distinguiéndose como acción destructiva.

### El submenú Sistema

Dos botones, en este orden: **"Suspender"** (tecla 1, azul de navegación) y **"Apagar"** (tecla 2, rojo de aviso). El orden es deliberado: la acción reversible se queda con la tecla más accesible y la irreversible no hereda la posición de la que se pulsa a menudo. El color hace el resto — el rojo sigue siendo exclusivo de "Apagar".

"Suspender" **no necesita ninguna regla `sudoers`**, al contrario que "Apagar": es una llamada USB al propio deck (`set_brightness`), no un comando del sistema. Lo de abajo aplica solo a "Apagar".

#### Apagado de la Pi

`deck.keys.shutdown_pi()` ejecuta `sudo -n shutdown -h now`. El servicio corre como `admin` **sin sudo**, así que hace falta una regla `NOPASSWD` en la Pi para que el comando no se quede pidiendo contraseña (el `-n` hace que falle rápido en vez de bloquear si la regla no está):

```bash
echo 'admin ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now' | sudo tee /etc/sudoers.d/streamdeck-habits-shutdown
sudo chmod 440 /etc/sudoers.d/streamdeck-habits-shutdown
```

**Regla ya aplicada en la Pi desplegada: el botón apaga sin problema.** Si algún día hay que reinstalar la Pi desde cero o mover el servicio a otra máquina, hay que reaplicar esta regla a mano (requiere contraseña interactiva, no se puede por SSH no interactivo desde aquí) o el botón fallará silenciosamente y quedará solo en `device_errors.log`, sin señal en la tecla, igual que el resto de errores de dispositivo.

## Operar la Raspberry Pi

Hay acceso SSH sin contraseña (clave pública ya autorizada) desde esta máquina: `ssh admin@RP3-MotoComm-1.local`. Puedes lanzar comandos directamente en la Pi (logs, estado del servicio, reinicio, despliegue, verificación) sin depender del usuario.

La resolución mDNS del `.local` es a veces intermitente: si un comando falla con "Could not resolve hostname", **reintenta antes de darlo por caído**.

**Confirma con el usuario antes de un despliegue normal** (`deploy/deploy.sh` sin `--test`, que hace `git pull`) **o de tocar `SUPABASE_ENV`**, salvo que ya lo haya pedido explícitamente en el mismo turno: eso sí toca `main`/producción de verdad. La verificación previa (compilar/importar en un temporal) no afecta al servicio y no necesita confirmación.

**Un despliegue en modo `--test` NO necesita confirmación previa**: cuando el usuario pida un cambio de código en este repo, impleméntalo y, al terminar, llévalo tú mismo a la Pi con `deploy.sh --test` (ver [Probar código sin mergear](#probar-código-sin-mergear-deploysh---test)) sin esperar autorización — sigue siendo el árbol de trabajo local sin commitear, reversible con `git checkout -- .` en la Pi, y no toca `main` ni `SUPABASE_ENV`. Lo que sigue necesitando autorización explícita del usuario, siempre, es el **paso 4** de esa sección (`git commit`/`git push` y el despliegue normal posterior) — eso no cambia.

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
  'd=$(mktemp -d); tar xf - -C "$d"; cd "$d"; /opt/streamdeck-habits/venv/bin/python -c "import orchestrator, provider.base, provider.supabase, core.screens, core.key_map, core.health, core.error_codes, core.emoji, deck.session, deck.primitives, deck.renderer, deck.keys; print(\"IMPORTS_OK\")"; rc=$?; cd /; rm -rf "$d"; exit $rc'
```

### Probar código sin mergear (`deploy.sh --test`)

Para validar en la Pi un cambio del árbol de trabajo local **antes de commitear/mergear a `main`**, sin que `git pull` machaque el código de prueba. **Los pasos 1-3 no necesitan autorización del usuario** (ver la nota en [Operar la Raspberry Pi](#operar-la-raspberry-pi)): tras implementar un cambio que el usuario haya pedido, ejecútalos tú mismo para dejarlo probado en la Pi tal como quedó el código.

> **`--test` es un modo de despliegue, no una base de datos.** Lo único que significa es "reinicia con el código que ya hay en disco, sin `git pull`". La Pi sigue leyendo el proyecto Supabase que diga `SUPABASE_ENV` — normalmente `main`, o sea **producción**, y así debe quedarse. Son dos ejes independientes: `deploy.sh --test` ≠ `SUPABASE_ENV=test`. Ver [Cambiar de proyecto Supabase](#cambiar-de-proyecto-supabase-main--test) para cuándo (no) tocar el otro eje.

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

> **El paso 4 lo autoriza el usuario, siempre.** Esta sección describe el procedimiento; **no** es permiso para ejecutarlo. **Nunca hagas `git commit` ni `git push` sin que el usuario lo pida explícitamente en ese momento**, por muy terminado y verificado que esté el trabajo. Que un plan aprobado liste "commit + push" entre sus pasos tampoco cuenta: aprobar el plan aprueba el trabajo, no la publicación. Al acabar, deja los cambios en el árbol de trabajo, resume qué cambió y pregunta. Si el usuario contesta algo asi como "despligue final" se ejecutará el paso 4: codigo a main y despliegue en la pi con el codigo limpio recien traido de lo que acaba de llegar a main.

**No dejes el paso 4 a medias.** Un `--test` que "fue bien" pero nunca se cerró con el despliegue normal deja la Pi con el árbol sucio y por detrás de `origin/main` indefinidamente — el servicio sigue corriendo con lo que haya en disco (puede coincidir con `main` por casualidad, o no) y nadie se entera hasta que alguien compara `git status`/`git log` a mano. Verificarlo cuesta un comando: `ssh admin@RP3-MotoComm-1.local 'cd /opt/streamdeck-habits && git status --short && git log --oneline -1'` debe salir vacío y en el mismo commit que `origin/main`. Si el usuario no autoriza el commit, **avisa de que la Pi queda así** en vez de resolverlo publicando por tu cuenta.

### Cambiar de proyecto Supabase (main / test)

Esto es **otra cosa** que el [despliegue en modo `--test`](#probar-código-sin-mergear-deploysh---test): aquel elige *qué código* corre en la Pi, este elige *contra qué base* habla. Se combinan libremente y lo normal es probar código nuevo (`--test`) contra `main`.

**Cuándo apuntar la Pi a `test`:**

- Cuando el cambio **toca la base**: una migración nueva en `../habits-core`, una vista o una RPC modificada, un `grant` que falta… Ahí no es opcional. Es el **paso 3** del proceso obligatorio que vive en `../habits-core/CLAUDE.md` → "El proceso de todo cambio de base de datos" (primera sección del fichero), y que resumido es: `develop` → PR a `test` → **probar aquí, en el deck, contra el proyecto de test** → PR a `main` → despliegue normal de la Pi y `SUPABASE_ENV` de vuelta a `main`. La rama `test` de `habits-core` es la que despliega a `habits-core-test`; `develop` es la de trabajo y no despliega sola. Ese fichero manda: si esto y aquello discrepan, gana `habits-core`.
- Cuando el usuario lo pida explícitamente.

**Cuándo no** (o sea, casi siempre): si el cambio es solo de funcionalidad del daemon — una vista nueva, un color, otra forma de repartir teclas, una pulsación que llama a una RPC **que ya existe y ya tiene su `grant`** —, no hay nada del contrato que validar y se prueba contra `main` como cualquier otro uso normal del deck. No cambies `SUPABASE_ENV` por tu cuenta ni lo ofrezcas como paso previo "por seguridad": escribir en producción es exactamente lo que hace el usuario cada vez que pulsa una tecla.

Si acabas apuntando a `test`, **devuélvela a `main` al terminar**: es fácil dejarla ahí y que el deck siga pintando datos de mentira días después.

**`habit_key_map.json` es único y no distingue de proyecto.** Cambiar `SUPABASE_ENV` no lo vacía ni lo cambia de fichero: al saltar a `test`, los hábitos de `main` que no existan ahí liberan su tecla y los de `test` reclaman las libres; al volver a `main`, sus hábitos vuelven a reclamar tecla **desde cero**, sin garantía de que sea la misma de antes de saltar a `test`. No es un bug — `update_mapping` hace exactamente lo que documenta ("nuevo hábito reclama la más baja libre") — pero conviene saberlo antes de sorprenderse de que un hábito cambió de sitio tras una prueba en `test`. Confirmado el 2026-08-05: al volver a `main` tras probar la pantalla "Crear", "Gym" reclamó la tecla 7.

**Pendiente de revisar**: si esto molesta en la práctica, la solución sería separar el mapeo por entorno (`habit_key_map_main.json` / `habit_key_map_test.json`, o una clave por `habit_id` que incluya el proyecto) en `core/key_map.py`. No implementado todavía — de momento se asume el barajeo tras cada préstamo a `test`.

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
Cambiar de proyecto es cambiar esa variable y reiniciar el servicio, nada mas.

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
- `checkin_failures.log` — JSON-lines de escrituras fallidas hacia Supabase: checkins de hábito, cierres de tarea y creaciones desde plantilla (errores de API, tecla en rojo); el campo `kind` distingue `"habit"` / `"task"` / `"template"`
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

El estándar está en `pyproject.toml` (ver también `.claude/skills/SKILL_Python_Code_Style_&_Documentation.md`):

- **`ruff`** — lint + formato (línea 120, comillas dobles, isort/pyupgrade/bugbear/simplify): `ruff check --fix .` y `ruff format .`
- **`mypy`** — comprobación de tipos (`target py313`; `StreamDeck.*` con `ignore_missing_imports` porque no publica stubs): `mypy .`
- **Type hints** en todas las APIs públicas, con `from __future__ import annotations` al principio de cada módulo (evaluación diferida: la anotación nunca se evalúa en runtime, así que es segura aunque apunte a algo no importado).
- **Docstrings estilo Google** (Args/Returns/Raises) en clases y funciones públicas.
- **Comentarios y mensajes de log en español.**
- **Todos los `print` con `flush=True`**: esto corre como servicio en segundo plano sin buffer (logging estilo journald/systemd).

## Mantén este fichero al día

Si al hacer un cambio descubres que algo descrito aquí no coincide con el comportamiento real del código (una descripción desactualizada, un comando que ya no funciona como se documenta, un color, una tecla o un valor que cambió), corrígelo **en el mismo turno** en vez de dejarlo pasar. Este documento solo es útil si las próximas sesiones pueden confiar en él sin verificarlo todo.
