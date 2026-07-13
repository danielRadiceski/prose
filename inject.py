"""Inject text into the focused app via clipboard paste, restoring the old clipboard."""

import time

import pyperclip
from pynput.keyboard import Controller, Key

_keyboard = Controller()


def inject(text: str) -> None:
    """Paste `text` into whatever window is focused; restore prior clipboard after."""
    if not text:
        return

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None  # non-text clipboard (e.g. image) — can't preserve it

    pyperclip.copy(text)
    time.sleep(0.05)  # let the target app see the new clipboard

    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("v")
        _keyboard.release("v")

    # Restore the user's clipboard after the paste has gone through
    time.sleep(0.3)
    if previous is not None:
        pyperclip.copy(previous)


if __name__ == "__main__":
    # Standalone test: focus a text field within 3 seconds
    print("Focus a text field... pasting in 3 seconds")
    time.sleep(3)
    inject("Hello from Prose! Clipboard should be restored after this.")
    print("Done — try Ctrl+V: your previous clipboard should still be there.")
