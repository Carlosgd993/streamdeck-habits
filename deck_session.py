import sys
import time

from StreamDeck.DeviceManager import DeviceManager

BRIGHTNESS = 60


class DeckSession:
    """Apertura/cierre/reconexion del dispositivo Stream Deck y gestion de
    brillo. No sabe nada de habitos ni de la API de TickTick."""

    def __init__(self, retries=30, delay=2):
        self._retries = retries
        self._delay = delay
        self.deck = None

    def _find_deck(self):
        for i in range(self._retries):
            decks = DeviceManager().enumerate()
            if decks:
                return decks[0]
            print(f"Sin Stream Deck detectada, reintento {i + 1}/{self._retries}", flush=True)
            time.sleep(self._delay)
        return None

    def open(self):
        deck = self._find_deck()
        if deck is None:
            print("No se encontro la Stream Deck tras los reintentos.", flush=True)
            sys.exit(1)
        deck.open()
        deck.reset()
        deck.set_brightness(BRIGHTNESS)
        self.deck = deck
        return self.deck

    def reconnect(self):
        """Repite la logica de apertura tras un fallo de dispositivo en
        marcha (nunca durante el arranque inicial, eso lo hace open())."""
        try:
            if self.deck is not None:
                self.deck.close()
        except Exception:
            pass
        return self.open()

    def close(self):
        if self.deck is not None:
            self.deck.reset()
            self.deck.close()
