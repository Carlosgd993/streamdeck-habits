#!/opt/streamdeck-habits/venv/bin/python
import sys
import time
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

COLOR_OFF = (30, 30, 200)   # azul
COLOR_ON  = (30, 200, 60)   # verde


def make_key_image(deck, color):
    image = PILHelper.create_image(deck, background=color)
    return PILHelper.to_native_format(deck, image)


def find_deck(retries=30, delay=2):
    for i in range(retries):
        decks = DeviceManager().enumerate()
        if decks:
            return decks[0]
        print(f"Sin Stream Deck detectada, reintento {i + 1}/{retries}", flush=True)
        time.sleep(delay)
    return None


def main():
    deck = find_deck()
    if deck is None:
        print("No se encontro la Stream Deck tras los reintentos.", flush=True)
        sys.exit(1)

    deck.open()
    deck.reset()
    deck.set_brightness(60)

    img_off = make_key_image(deck, COLOR_OFF)
    img_on = make_key_image(deck, COLOR_ON)
    state = {k: False for k in range(deck.key_count())}

    for k in range(deck.key_count()):
        deck.set_key_image(k, img_off)

    def on_key_change(deck, key, pressed):
        if not pressed:
            return
        state[key] = not state[key]
        deck.set_key_image(key, img_on if state[key] else img_off)
        print(f"Tecla {key} -> {'ON' if state[key] else 'OFF'}", flush=True)

    deck.set_key_callback(on_key_change)

    try:
        while True:
            time.sleep(1)
    finally:
        deck.reset()
        deck.close()


if __name__ == "__main__":
    main()
