#!/bin/bash
# deploy.sh -- despliega el daemon en la Raspberry Pi.
#
#   deploy.sh          Despliegue normal: git pull + reinicio del servicio.
#   deploy.sh --test   Igual PERO sin git pull (usa el codigo ya en disco).
#                      Para probar codigo copiado a mano (p.ej. via tar desde la
#                      maquina de desarrollo) antes de mergearlo a main.
#
# Ambos modos reinician igual: paran el proceso principal (corre como 'admin')
# y systemd lo relanza por su politica Restart=on-failure, cargando el codigo
# nuevo. No usan sudo, asi que se pueden lanzar de forma no interactiva por SSH.
#
# Este script NO instala cambios de la unit de systemd
# (deploy/streamdeck-habits.service). Si cambias ese fichero, instalalo a mano:
#   sudo cp deploy/streamdeck-habits.service /etc/systemd/system/ \
#     && sudo systemctl daemon-reload && sudo systemctl restart streamdeck-habits.service
#
# Si en --test el codigo casca en bucle y systemd lo deja parado, arrancalo con:
#   sudo systemctl start streamdeck-habits.service
set -e

REPO_DIR="/opt/streamdeck-habits"
SERVICE_NAME="streamdeck-habits.service"

TEST=0
for arg in "$@"; do
    case "$arg" in
        --test) TEST=1 ;;
        *) echo "Argumento desconocido: $arg (uso: deploy.sh [--test])" >&2; exit 2 ;;
    esac
done

cd "$REPO_DIR"

if [ "$TEST" -eq 0 ]; then
    echo "Trayendo cambios del repo..."
    echo "Sincronizando con origin/main (se descartan cambios locales)..."
    git fetch origin
    git reset --hard origin/main
else
    echo "Modo --test: se usa el codigo ya presente en $REPO_DIR (sin git pull)."
fi

# Reinicio (identico en ambos modos): matar el proceso y dejar que systemd lo
# relance por Restart=on-failure, cargando el codigo nuevo. Sin sudo.
pid=$(systemctl show -p MainPID --value "$SERVICE_NAME")
if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    echo "El servicio no esta corriendo (MainPID=0)." >&2
    echo "Arrancalo con: sudo systemctl start $SERVICE_NAME" >&2
    exit 1
fi

echo "Reiniciando: matando el proceso principal ($pid); systemd lo relanzara (Restart=on-failure)..."
kill -9 "$pid"

echo "Esperando a que systemd relance el servicio con un PID nuevo..."
new_pid=""
for _ in $(seq 1 20); do
    sleep 1
    state=$(systemctl show -p ActiveState --value "$SERVICE_NAME")
    cur=$(systemctl show -p MainPID --value "$SERVICE_NAME")
    if [ "$state" = "active" ] && [ -n "$cur" ] && [ "$cur" != "0" ] && [ "$cur" != "$pid" ]; then
        new_pid="$cur"
        break
    fi
done

if [ -z "$new_pid" ]; then
    echo "El servicio no volvio a arrancar a tiempo. Revisa:" >&2
    echo "  systemctl status $SERVICE_NAME" >&2
    echo "  journalctl -u $SERVICE_NAME -n 50" >&2
    exit 1
fi

echo "Relanzado con exito. PID nuevo: $new_pid"
echo "Estado actual:"
systemctl status "$SERVICE_NAME" --no-pager
