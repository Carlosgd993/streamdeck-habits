"""Gestion del ciclo de vida del dispositivo Stream Deck (apertura, cierre,
reconexion y brillo), sin conocimiento de habitos ni del proveedor de datos."""

from __future__ import annotations

import sys
import time
from typing import Any

from StreamDeck.DeviceManager import DeviceManager

BRIGHTNESS = 60
# Minimo con el que el icono de stand by se sigue distinguiendo. No es 0 a
# proposito: a 0 el deck parece apagado o colgado, y la idea es que se note que
# esta suspendido. El brillo es global (no hay control por tecla), asi que este
# valor ilumina tenuemente las 15; subirlo se come parte del ahorro casi en
# proporcion directa. Es la constante a tocar para ajustarlo a ojo.
BRIGHTNESS_STANDBY = 10


class DeckSession:
    """Apertura/cierre/reconexion del dispositivo Stream Deck y gestion de
    brillo. No sabe nada de habitos ni del proveedor de datos.

    Attributes:
        deck: El dispositivo abierto, o ``None`` mientras no se haya abierto.
    """

    def __init__(self, retries: int = 30, delay: int = 2) -> None:
        """Inicializa la sesion (no abre el dispositivo todavia).

        Args:
            retries: Numero de reintentos de deteccion del dispositivo.
            delay: Segundos de espera entre reintentos.
        """
        self._retries = retries
        self._delay = delay
        self.deck: Any = None

    def _find_deck(self) -> Any:
        for i in range(self._retries):
            decks = DeviceManager().enumerate()
            if decks:
                return decks[0]
            print(f"Sin Stream Deck detectada, reintento {i + 1}/{self._retries}", flush=True)
            time.sleep(self._delay)
        return None

    def open(self) -> Any:
        """Detecta y abre el dispositivo, lo resetea y fija el brillo.

        Termina el proceso (``sys.exit(1)``) si no se encuentra ninguna
        Stream Deck tras agotar los reintentos.

        Returns:
            El dispositivo Stream Deck abierto.
        """
        deck = self._find_deck()
        if deck is None:
            print("No se encontro la Stream Deck tras los reintentos.", flush=True)
            sys.exit(1)
        deck.open()
        deck.reset()
        self.deck = deck
        self.set_brightness(BRIGHTNESS)
        return self.deck

    def set_brightness(self, percent: int) -> None:
        """Fija el brillo de la retroiluminacion (0-100).

        Es la unica palanca de consumo real que tiene el daemon, y no necesita
        privilegios: apagarla (``BRIGHTNESS_STANDBY``) es lo que hace el modo
        stand by. No toca las imagenes ya pintadas ni el hilo de lectura del
        deck, asi que una tecla pulsada con el brillo a 0 se sigue recibiendo
        igual -- que es justo lo que permite despertarlo.

        Args:
            percent: Brillo en tanto por ciento. La libreria ya lo satura al
                rango valido.
        """
        if self.deck is not None:
            self.deck.set_brightness(percent)

    def reconnect(self) -> Any:
        """Repite la logica de apertura tras un fallo de dispositivo en
        marcha (nunca durante el arranque inicial, eso lo hace open())."""
        try:
            if self.deck is not None:
                self.deck.close()
        except Exception:
            pass
        return self.open()

    def close(self) -> None:
        """Resetea y cierra el dispositivo si estaba abierto."""
        if self.deck is not None:
            self.deck.reset()
            self.deck.close()
