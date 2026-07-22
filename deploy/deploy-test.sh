#!/bin/bash
# deploy-test.sh -- reinicia el servicio con el codigo que YA esta en
# /opt/streamdeck-habits, SIN git pull y SIN sudo.
#
# Pensado para probar en la Pi codigo copiado a mano (p.ej. via tar desde la
# maquina de desarrollo) antes de mergearlo a main. A diferencia de deploy.sh:
#   - No hace 'git pull' (no machaca el codigo de prueba ni falla si el arbol
#     esta sucio).
#   - No usa sudo: mata el proceso principal (corre como 'admin', igual que
#     este script) y deja que systemd lo relance por su politica
#     Restart=on-failure. Asi se puede lanzar de forma no interactiva por SSH.
#
# Requisitos / limitaciones:
#   - La unit debe tener Restart=on-failure (o always) y un RestartSec corto;
#     asi lo tiene streamdeck-habits.service.
#   - Si el codigo nuevo falla al arrancar y systemd agota StartLimitBurst, el
#     servicio queda parado y arrancarlo de nuevo si necesita sudo:
#         sudo systemctl start streamdeck-habits.service
#   - Para revertir al codigo de main tras una prueba fallida:
#         git -C /opt/streamdeck-habits checkout -- . && deploy/deploy-test.sh
set -e

SERVICE_NAME="streamdeck-habits.service"

pid=$(systemctl show -p MainPID --value "$SERVICE_NAME")
if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    echo "El servicio no esta corriendo (MainPID=0)." >&2
    echo "Arrancalo con: sudo systemctl start $SERVICE_NAME" >&2
    exit 1
fi

echo "Matando el proceso principal ($pid); systemd lo relanzara (Restart=on-failure)..."
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
