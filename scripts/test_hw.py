#!/usr/bin/env python3
from StreamDeck.DeviceManager import DeviceManager
import time

decks = DeviceManager().enumerate()
print(f"Dispositivos encontrados: {len(decks)}")
if not decks:
    raise SystemExit("No se detectó ningún Stream Deck. Revisa udev + reconexión USB.")

deck = decks[0]
deck.open()
deck.reset()

print("Modelo:", deck.deck_type())
print("Serial:", deck.get_serial_number())
print("Firmware:", deck.get_firmware_version())
print("Nº de teclas:", deck.key_count())

deck.set_brightness(30)

def on_key_change(deck, key, state):
    print(f"Tecla {key}: {'pulsada' if state else 'soltada'}")

deck.set_key_callback(on_key_change)

print("Pulsa cualquier tecla física (Ctrl+C para salir)...")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    deck.set_brightness(0)
    deck.close()
