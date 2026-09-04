# streamdeck-habits

Daemon en Python que convierte una Elgato Stream Deck fisica en un mando de
seguimiento de habitos y tareas sobre una base de datos propia en
[Supabase](https://supabase.com) (repo hermano `../habits-core`). Corre 24/7
como servicio systemd en una Raspberry Pi y sondea la base via PostgREST cada
15 minutos.

Es un **menu de pantallas**, no una sola vista: por defecto muestra "Hoy"
(habitos pendientes en blanco, hechos en gris oscuro; tareas coloreadas por
prioridad), y desde el menu se llega a "Habitos" (con deshacer), "Tareas",
"Crear" (instancia una tarea desde una plantilla), "Logs" (habitos de solo
registro), "Cronometros" (cronometros tipo Toggl Track: un boton por etiqueta
rapida o por tarea que alterna iniciar/detener, con como mucho uno corriendo a
la vez) y un submenu "Sistema" con la suspension de la pantalla y el apagado
de la Pi. Un habito cuantificable marcado `manual_entry` (p.ej. "Peso") abre
un teclado numerico en vez de sumar de uno en uno. Detalle completo de
pantallas y flujo en [CLAUDE.md](CLAUDE.md).

## Stand by

Tras 30 minutos sin pulsar nada (`STANDBY_SECONDS` en [config.py](config.py)),
o pulsando "Suspender" en el submenu Sistema, el deck entra en **stand by**:
baja la retroiluminacion al minimo, pinta una pantalla fija con un icono y deja
de sondear la base. Cualquier tecla lo despierta — y esa pulsacion **solo**
despierta, nunca ejecuta lo que hubiera debajo, asi que encenderlo a ciegas no
cierra una tarea por accidente.

Es la unica palanca de consumo real del daemon: los 15 backlights son el grueso
de los ~1,5 W que gasta el deck, asi que bajarlos ahorra del orden de 0,7-0,9 W
sobre los ~2,5-3 W del conjunto Pi+deck. El brillo minimo no es 0 a proposito,
para distinguir "suspendida" de "apagada".

Que se ve mientras esta suspendido sale entero de `STANDBY_LAYOUT`
(`core/screens.py`), un dict `tecla -> StandbyKey(label, emoji)`: editar esa
tabla es lo unico que hace falta para cambiarlo. Ver
[CLAUDE.md → Stand by](CLAUDE.md#stand-by).

## Instalacion

No hay `requirements.txt`; las cuatro dependencias de terceros se instalan a
mano en un entorno virtual:

```bash
python -m venv venv
source venv/bin/activate
pip install streamdeck python-dotenv requests Pillow
```

## Configuracion

El daemon lee su configuracion de un fichero `.env` junto al codigo (ver
[.env.example](.env.example)). Trae credenciales de **dos** proyectos Supabase
a la vez (produccion y test) y `SUPABASE_ENV` elige cual usa `SupabaseProvider`:

| Variable                        | Descripcion                                         |
| -------------------------------- | --------------------------------------------------- |
| `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY` | Proyecto `main` (produccion), sin sufijo. |
| `SUPABASE_URL_TEST` / `SUPABASE_PUBLISHABLE_KEY_TEST` | Proyecto `test`, para validar cambios de esquema antes de produccion. |
| `SUPABASE_ENV`                   | `main` o `test`: cual de los dos pares usa el daemon. |

Cambiar de proyecto es cambiar esa variable y reiniciar el servicio — ver
[CLAUDE.md → Cambiar de proyecto Supabase](CLAUDE.md#cambiar-de-proyecto-supabase-main--test).

Las rutas de ejecucion (`BASE_DIR = /opt/streamdeck-habits`, logs, mapeo de
teclas) estan fijadas en [config.py](config.py). En la Pi desplegada se generan
solos, junto al codigo, estos ficheros (todos ignorados por git):

- `habit_key_map.json` — mapeo persistido `habit_id -> key_index`.
- `checkin_failures.log` — log JSON-lines de checkins fallidos hacia Supabase.
- `device_errors.log` — log de texto plano de fallos del propio Stream Deck.

## Uso

Los tres puntos de entrada requieren acceso al dispositivo USB fisico:

```bash
python orchestrator.py         # daemon principal
python scripts/test_hw.py      # smoke test de hardware (modelo/serie/firmware)
python scripts/toggle_test.py  # smoke test visual (alterna azul/verde, sin red)
```

## Despliegue

El daemon corre como servicio systemd en una Raspberry Pi. `deploy/deploy.sh`
hace `git pull` y reinicia el servicio (`Restart=on-failure`); acepta
`--test` para reiniciar sin `git pull` (probar codigo aun no mergeado). No
instala cambios de la unit de systemd
(`deploy/streamdeck-habits.service`) — si se modifica ese fichero, hay que
copiarlo a mano a `/etc/systemd/system/` con `sudo` + `daemon-reload` +
`restart`.

## Desarrollo

El codigo se edita en la maquina de desarrollo (sin Python ni hardware) y se
prueba en la Pi. El estilo se apoya en `ruff` y `mypy`, configurados en
[pyproject.toml](pyproject.toml):

```bash
pip install ruff mypy
ruff check --fix .   # lint + auto-fix
ruff format .        # formateo
mypy .               # comprobacion de tipos
```

### Convenciones

- Los mensajes de log de cara al usuario y los comentarios estan en espanol.
- Todos los `print` usan `flush=True` (corre como servicio sin buffer, estilo
  journald/systemd).
- Docstrings estilo Google y type hints en las APIs publicas.

## Arquitectura

El codigo esta organizado en tres capas por carpetas, con una regla de
dependencia clara:

- **`provider/`** — la API, aislada tras cuatro puertos abstractos (`base.py`:
  `HabitProvider`, `TaskProvider`, `TemplateProvider`, `TimerProvider` —
  separados porque un backend puede ofrecer una capacidad sin las otras;
  modelos de dominio `Habit`/`Task`/`Template`/`TimerLabel`/`RunningTimer` y
  excepciones agnosticas). Supabase es solo un adaptador (`supabase.py`) que
  implementa los cuatro detras de esos puertos; sustituirlo por otra API es
  escribir otro adaptador y cambiar una linea en `orchestrator.py`. La logica
  de negocio (que dia es hoy, cual es el siguiente valor de un habito, que
  tareas tocan, cuando arranca/para un cronometro) vive en la base de datos,
  no aqui: el daemon es un renderizador tonto sobre un contrato de vistas y
  funciones.
- **`deck/`** — todo lo de la Stream Deck (hardware y pintado): `session`
  (incluido el brillo, y con el la suspension), `primitives`, `renderer`,
  `keys`, `style`. No sabe nada del proveedor de datos.
- **`core/`** — dominio/orquestacion agnosticos: `screens` (el registro de
  menu/pantallas y su resolucion a teclas — el eje de todo el sistema de
  navegacion), `key_map`, `health`, `error_codes`, `emoji`.

`orchestrator.py` (punto de entrada) y `config.py` viven en la raiz y coordinan
las tres capas hablando solo con abstracciones. Ver [CLAUDE.md](CLAUDE.md)
para el detalle de cada modulo y de las pantallas.
