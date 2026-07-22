"""Gestion del ciclo de vida del dispositivo Stream Deck (apertura, cierre,
reconexion y brillo), sin conocimiento de habitos ni de la API de TickTick."""

from __future__ import annotations

import sys
import time
from typing import Any

from StreamDeck.DeviceManager import DeviceManager

BRIGHTNESS = 60


class DeckSession:
    """Apertura/cierre/reconexion del dispositivo Stream Deck y gestion de
    brillo. No sabe nada de habitos ni de la API de TickTick.

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
        deck.set_brightness(BRIGHTNESS)
        self.deck = deck
        return self.deck

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
