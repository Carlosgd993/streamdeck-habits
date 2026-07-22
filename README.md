# streamdeck-habits

Daemon en Python que convierte una Elgato Stream Deck fisica en un mando de
seguimiento de habitos para [TickTick](https://ticktick.com). Corre en una
Raspberry Pi, sondea la API abierta de TickTick, ilumina cada tecla en blanco
con texto negro grande (pendiente) o en gris oscuro con texto atenuado (hecho
hoy, para que pase desapercibido frente a lo pendiente), y envia un checkin a
TickTick al pulsarla.

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
[.env.example](.env.example)):

| Variable                | Descripcion                                            |
| ----------------------- | ------------------------------------------------------ |
| `TICKTICK_ACCESS_TOKEN` | Token de acceso de la API abierta de TickTick. Se regenera a mano cuando caduca (no hay flujo de refresh). |

Las rutas de ejecucion (`BASE_DIR = /opt/streamdeck-habits`, logs, mapeo de
teclas) estan fijadas en [config.py](config.py). En la Pi desplegada se generan
solos, junto al codigo, estos ficheros (todos ignorados por git):

- `habit_key_map.json` — mapeo persistido `habit_id -> key_index`.
- `checkin_failures.log` — log JSON-lines de checkins fallidos hacia TickTick.
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

`orchestrator.py` es el punto de entrada y coordina el resto de modulos:
`config`, `auth`, `ticktick_client`, `habit_key_map`, `habits/` (jerarquia de
tipos de habito), `error_codes`, `health`, `render_primitives`,
`deck_renderer`, `special_keys` y `deck_session`. Ver
[.claude/CLAUDE.md](.claude/CLAUDE.md) para el detalle de cada uno.
