"""Prose — hold/press a hotkey, speak, and cleaned-up text is typed into the focused app.

Pipeline: hotkey -> record mic -> Groq Whisper transcription -> Claude cleanup -> paste.
"""

import ctypes
import os
import queue
import signal
import sys
import threading
import time
import traceback

from pynput import keyboard

import config
import first_run
from audio import Recorder
from cleanup import cleanup
from inject import inject
from overlay import Overlay
from transcribe import transcribe
from tray import Tray

LOG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Prose", "prose.log")

# The packaged exe is windowed (no console), so send all output to a log file.
if getattr(sys, "frozen", False):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        pass

# Never crash on console output, whatever the terminal's codepage is
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


class AppState:
    def __init__(self):
        self.enabled = True
        self.cleanup_enabled = config.CLEANUP_ENABLED


state = AppState()
recorder = Recorder()
tray: Tray | None = None
overlay: Overlay | None = None
_listener = None
_quit_event = threading.Event()
_actions: "queue.Queue[str]" = queue.Queue()
# Set while inject() is synthesising Ctrl+V, so the hotkey listener ignores our
# own keystrokes instead of treating them as the user pressing the hotkey.
_injecting = threading.Event()

_OVERLAY_MODES = {"listening": "listening", "processing": "processing"}


def _set_status(status: str, tooltip: str | None = None) -> None:
    """Update both the tray icon and the floating overlay."""
    if tray is not None:
        try:
            tray.set_state(status, tooltip)
        except Exception:
            pass
    if overlay is not None:
        overlay.set_mode(_OVERLAY_MODES.get(status, "hidden"))


def _print_error(e: Exception) -> None:
    if os.getenv("PROSE_DEBUG"):
        traceback.print_exc()
    else:
        print(f"[error] {type(e).__name__}: {e}")


def _process(wav_bytes: bytes) -> None:
    """Transcribe -> (optionally) clean -> inject. Runs on its own thread."""
    try:
        _set_status("processing")
        text = transcribe(wav_bytes)
        print(f"[transcript] {text}")
        if text:
            if state.cleanup_enabled:
                _set_status("processing", "Prose - cleaning up...")
                try:
                    text = cleanup(text)
                    print(f"[cleaned]    {text}")
                except Exception as e:
                    # Cleanup is a nicety; never lose the user's words over it.
                    _print_error(e)
                    print("[cleanup]    failed - pasting raw transcript instead")
            _injecting.set()  # deafen the hotkey listener to our own Ctrl+V
            try:
                inject(text)
            finally:
                time.sleep(0.15)  # let the synthetic key events drain through the hook
                _injecting.clear()
    except Exception as e:
        _print_error(e)
        _set_status("idle", "Prose - error (see console)")
        return
    _set_status("idle" if state.enabled else "disabled")


def _worker() -> None:
    """Executes start/stop actions off the keyboard-hook thread.

    Opening the mic stream can take ~1s (Bluetooth) and can raise — neither may
    happen inside a pynput callback, or the hook lags/dies.
    """
    while True:
        action = _actions.get()
        if action == "quit":
            return
        try:
            if action == "start":
                if state.enabled and not recorder.is_recording:
                    recorder.start()
                    _set_status("listening")
                    print("[recording] started")
            elif action == "stop":
                if recorder.is_recording:
                    wav = recorder.stop()
                    print("[recording] stopped")
                    if wav is None:
                        print("[recording] too short or silent - ignored")
                        _set_status("idle" if state.enabled else "disabled")
                    else:
                        threading.Thread(target=_process, args=(wav,), daemon=True).start()
        except Exception as e:
            _print_error(e)
            _set_status("idle", "Prose - error (see console)")


# --- hotkey handling (callbacks only enqueue; all work happens on the worker) ---


def _toggle_recording() -> None:
    _actions.put("stop" if recorder.is_recording else "start")


# Hold-mode combos are matched against the OS's real key state (GetAsyncKeyState)
# rather than a set we accumulate ourselves. Accumulating is fragile: miss a single
# key-up event and the key is "stuck down" forever, so a later lone Ctrl press would
# silently complete the combo and start a phantom recording.
_VK = {
    "ctrl": (0x11,),                 # VK_CONTROL (either side)
    "win": (0x5B, 0x5C),             # VK_LWIN / VK_RWIN
    "cmd": (0x5B, 0x5C),
    "super": (0x5B, 0x5C),
    "alt": (0x12,),                  # VK_MENU
    "shift": (0x10,),                # VK_SHIFT
    "ctrl_l": (0xA2,), "ctrl_r": (0xA3,),
    "shift_l": (0xA0,), "shift_r": (0xA1,),
    "alt_l": (0xA4,), "alt_r": (0xA5,),
    "space": (0x20,), "tab": (0x09,), "caps_lock": (0x14,),
}
_WIN_VKS = {0x5B, 0x5C}

# An unassigned virtual key. Windows opens the Start menu when Win goes UP and no
# other key was pressed *while it was down* — pressing Ctrl beforehand doesn't count.
# Tapping this no-op key while Win is held makes Windows treat it as a combo, so the
# Start menu stays shut. It produces no character and apps ignore it.
_VK_NOOP = 0xE8

_user32 = ctypes.windll.user32
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
_user32.VkKeyScanW.restype = ctypes.c_short


def _key_is_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


# The pynput key objects Windows may report for each combo part. Needed as well as
# the VKs above because a low-level hook runs BEFORE Windows updates the async key
# state — so GetAsyncKeyState can't see the very key that triggered the callback.
# We therefore trust the event for the key that just changed, and the OS for the rest.
_KEY_VARIANTS = {
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "win": ("cmd", "cmd_l", "cmd_r"),
    "cmd": ("cmd", "cmd_l", "cmd_r"),
    "super": ("cmd", "cmd_l", "cmd_r"),
    "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
    "alt_r": ("alt_r", "alt_gr"),
    "shift": ("shift", "shift_l", "shift_r"),
}


def _resolve_hold_combo() -> tuple[list[frozenset], list[tuple[int, ...]]]:
    """Parse HOLD_KEY ('ctrl+win', 'f9', ...) into aligned key-object and VK groups.

    The combo is held when every group has at least one of its keys down.
    """
    key_groups, vk_groups = [], []
    for part in config.HOLD_KEY.split("+"):
        part = part.strip()

        # VK group — ground truth, read from the OS
        if part in _VK:
            vks = _VK[part]
        elif len(part) > 1 and part[0] == "f" and part[1:].isdigit():
            vks = (0x70 + int(part[1:]) - 1,)  # VK_F1..VK_F24
        elif len(part) == 1:
            vks = (_user32.VkKeyScanW(part) & 0xFF,)
        else:
            raise ValueError(f"Unknown key in HOLD_KEY: {part!r}")

        # key-object group — matches the event pynput hands us
        keys = set()
        for name in _KEY_VARIANTS.get(part, (part,)):
            if hasattr(keyboard.Key, name):
                keys.add(getattr(keyboard.Key, name))
            elif len(name) == 1:
                keys.add(keyboard.KeyCode.from_char(name))
        if not keys:
            raise ValueError(f"Unknown key in HOLD_KEY: {part!r}")

        key_groups.append(frozenset(keys))
        vk_groups.append(vks)
    return key_groups, vk_groups


def _start_hotkey_listener():
    global _listener
    if config.HOTKEY_MODE == "hold":
        key_groups, vk_groups = _resolve_hold_combo()
        has_win = any(vk in _WIN_VKS for g in vk_groups for vk in g)
        kb = keyboard.Controller()
        noop = keyboard.KeyCode.from_vk(_VK_NOOP)
        combo_down = False  # our intent; the recorder state is the worker's business

        def combo_complete(pressed) -> bool:
            """Is every part of the combo down, given `pressed` just went down?

            The hook fires before Windows updates the async key state, so the key we
            were just handed won't show as down yet — count it explicitly, and read
            the OS for the others. Reading the OS (rather than a set we maintain) is
            what stops a single missed key-up from leaving a key stuck "down" forever
            and letting a lone Ctrl fire the hotkey.
            """
            return all(
                pressed in kg or any(_key_is_down(vk) for vk in vg)
                for kg, vg in zip(key_groups, vk_groups)
            )

        def tap_noop():
            """Send the no-op key that keeps the Start menu shut.

            Must NOT run on the hook thread: synthesising a key from inside a
            low-level keyboard hook callback serialises against the hook itself and
            wedges the listener, so it silently stops delivering events.
            """
            kb.press(noop)
            kb.release(noop)

        def on_press(key):
            nonlocal combo_down
            # Ignore keystrokes we synthesised ourselves. Without this, the Ctrl+V
            # from inject() re-completes the combo while Win is still held and kicks
            # off a phantom recording.
            if _injecting.is_set() or combo_down or not combo_complete(key):
                return
            combo_down = True
            if has_win:
                threading.Thread(target=tap_noop, daemon=True).start()
            _actions.put("start")

        def on_release(key):
            # A key-up is definitive — no need to consult the OS. If any part of the
            # combo was let go, the user has stopped dictating.
            nonlocal combo_down
            if _injecting.is_set() or not combo_down:
                return
            if any(key in kg for kg in key_groups):
                combo_down = False
                _actions.put("stop")

        _listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    else:
        _listener = keyboard.GlobalHotKeys({config.TOGGLE_HOTKEY: _toggle_recording})
    _listener.start()


def _request_quit():
    _quit_event.set()


def _shutdown():
    if _listener is not None:
        _listener.stop()
    _actions.put("quit")
    if recorder.is_recording:
        try:
            recorder.stop()
        except Exception:
            pass
    if tray is not None:
        try:
            tray.icon.stop()
        except Exception:
            pass


def main():
    global tray, overlay
    if first_run.missing_keys():
        # No keys yet (fresh install, or someone else's machine): collect their own.
        print(f"[setup] missing {', '.join(first_run.missing_keys())} - showing setup dialog")
        if not first_run.prompt_for_keys():
            print("[setup] cancelled")
            sys.exit(1)
        state.cleanup_enabled = config.CLEANUP_ENABLED
        print(f"[setup] keys saved to {config.USER_ENV_PATH}")

    if config.HOTKEY_MODE == "hold":
        print(f"Prose running - hold [{config.HOLD_KEY}] to dictate.")
    else:
        print(f"Prose running - press [{config.TOGGLE_HOTKEY}] to start/stop dictation.")
    print("Quit: Ctrl+C here, or right-click the tray icon.")

    # Ctrl+C / Ctrl+Break -> graceful quit. An explicit handler is more reliable
    # than KeyboardInterrupt when background threads (tray, hooks) are running.
    def _sig_handler(signum, frame):
        print("\n[exit] interrupted")
        _quit_event.set()

    signal.signal(signal.SIGINT, _sig_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _sig_handler)

    threading.Thread(target=_worker, daemon=True).start()
    tray = Tray(state, on_quit=_request_quit)
    _start_hotkey_listener()
    tray.icon.run_detached()  # tray runs on its own thread; main thread stays interruptible

    try:
        if config.OVERLAY_ENABLED:
            # Tk must own the main thread; its 40ms tick keeps signal handlers live
            overlay = Overlay(recorder, _quit_event)
            overlay.run()  # blocks until _quit_event is set
        else:
            while not _quit_event.is_set():
                time.sleep(0.2)
        print("[exit] shutting down")
    except KeyboardInterrupt:
        print("\n[exit] Ctrl+C")
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
