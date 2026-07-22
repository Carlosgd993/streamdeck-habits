#!/bin/bash
set -e

REPO_DIR="/opt/streamdeck-habits"
SERVICE_NAME="streamdeck-habits.service"
SERVICE_TEMPLATE="$REPO_DIR/deploy/$SERVICE_NAME"
SERVICE_TARGET="/etc/systemd/system/$SERVICE_NAME"

# --nopull: redespliega el codigo que ya esta en disco sin traer cambios del
# repo (util para probar sin machacar codigo copiado a mano). Para una prueba
# sin sudo, usa deploy/deploy-test.sh en su lugar.
PULL=1
for arg in "$@"; do
    case "$arg" in
        --nopull|--no-pull) PULL=0 ;;
        *) echo "Argumento desconocido: $arg (uso: deploy.sh [--nopull])" >&2; exit 2 ;;
    esac
done

cd "$REPO_DIR"

if [ "$PULL" -eq 1 ]; then
    echo "Trayendo cambios del repo..."
    git pull
else
    echo "Modo --nopull: se usa el codigo ya presente en $REPO_DIR (sin git pull)."
fi

echo "Actualizando unit de systemd..."
sudo cp "$SERVICE_TEMPLATE" "$SERVICE_TARGET"
sudo systemctl daemon-reload

echo "Reiniciando servicio..."
sudo systemctl restart "$SERVICE_NAME"

echo "Listo. Estado actual:"
systemctl status "$SERVICE_NAME" --no-pager