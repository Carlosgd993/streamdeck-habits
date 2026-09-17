#!/usr/bin/env python3
"""Reset USB del Stream Deck, para cuando se queda colgado a nivel HID.

Sintoma: el servicio entra en bucle de reinicio y el journal repite
``TransportError: Failed to write feature report (-1)`` desde
``deck/session.py`` -> ``deck.reset()``, seguido de un assert de libusb
(``usbi_mutex_lock``) que mata el proceso. El deck sigue apareciendo en
``lsusb``, pero rechaza cualquier escritura: no es un fallo del daemon, es el
dispositivo atascado. Pasa tras encadenar varios reinicios bruscos del
servicio (``deploy.sh`` mata el proceso con ``kill -9``, y si pilla una
transferencia USB a medias el deck se queda en ese estado).

Este script manda el ioctl ``USBDEVFS_RESET`` al nodo del dispositivo -- lo
mismo que hace el clasico ``usbreset.c``, y el equivalente por software a
desenchufarlo y volverlo a enchufar. **No necesita sudo**: el nodo de
``/dev/bus/usb`` lleva una ACL que da acceso al usuario del servicio.

Uso en la Pi (el servicio puede estar en su bucle de reinicio, no molesta):

    /opt/streamdeck-habits/venv/bin/python /opt/streamdeck-habits/scripts/usbreset.py

El siguiente reintento de systemd deberia abrir el deck ya sin problema.
"""

from __future__ import annotations

import fcntl
import os
import re
import subprocess
import sys

USBDEVFS_RESET = ord("U") << 8 | 20  # ioctl de linux/usbdevice_fs.h
_ELGATO_VENDOR_ID = "0fd9"  # Elgato Systems, tal y como lo imprime lsusb


def find_deck() -> str | None:
    """Localiza el nodo USB del Stream Deck.

    Returns:
        La ruta tipo ``/dev/bus/usb/001/004``, o ``None`` si el dispositivo no
        aparece en ``lsusb`` (desconectado, o sin alimentacion).
    """
    out = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f"{_ELGATO_VENDOR_ID}:" not in line:
            continue
        match = re.match(r"Bus (\d+) Device (\d+):", line)
        if match:
            return f"/dev/bus/usb/{match.group(1)}/{match.group(2)}"
    return None


def main() -> None:
    """Resetea el deck, o sale con codigo 1 si no se encuentra."""
    path = find_deck()
    if path is None:
        print("No se encontro ningun Stream Deck en lsusb", flush=True)
        sys.exit(1)

    fd = os.open(path, os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)
    print(f"Reset USB enviado a {path}", flush=True)


if __name__ == "__main__":
    main()
