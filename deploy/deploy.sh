#!/bin/bash
set -e

REPO_DIR="/opt/streamdeck-habits"
SERVICE_NAME="streamdeck-habits.service"
SERVICE_TEMPLATE="$REPO_DIR/deploy/$SERVICE_NAME"
SERVICE_TARGET="/etc/systemd/system/$SERVICE_NAME"

cd "$REPO_DIR"

echo "Trayendo cambios del repo..."
git pull

echo "Actualizando unit de systemd..."
sudo cp "$SERVICE_TEMPLATE" "$SERVICE_TARGET"
sudo systemctl daemon-reload

echo "Reiniciando servicio..."
sudo systemctl restart "$SERVICE_NAME"

echo "Listo. Estado actual:"
systemctl status "$SERVICE_NAME" --no-pager