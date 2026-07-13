"""Prose — hold/press a hotkey, speak, and cleaned-up text is typed into the focused app.

Pipeline: hotkey -> record mic -> Groq Whisper transcription -> Claude cleanup -> paste.
"""

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
            inject(text)
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


# Modifier aliases -> every key object Windows/pynput may report for them
_KEY_VARIANTS = {
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "win": ("cmd", "cmd_l", "cmd_r"),
    "cmd": ("cmd", "cmd_l", "cmd_r"),
    "super": ("cmd", "cmd_l", "cmd_r"),
    "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
    "alt_r": ("alt_r", "alt_gr"),
    "shift": ("shift", "shift_l", "shift_r"),
}


def _resolve_hold_combo() -> list[frozenset]:
    """Parse HOLD_KEY like 'ctrl+win' or 'f9' into one variant-set per combo part."""
    groups = []
    for part in config.HOLD_KEY.split("+"):
        part = part.strip()
        names = _KEY_VARIANTS.get(part, (part,))
        keys = set()
        for name in names:
            if hasattr(keyboard.Key, name):
                keys.add(getattr(keyboard.Key, name))
            elif len(name) == 1:
                keys.add(keyboard.KeyCode.from_char(name))
        if not keys:
            raise ValueError(f"Unknown key in HOLD_KEY: {part!r}")
        groups.append(frozenset(keys))
    return groups


def _start_hotkey_listener():
    global _listener
    if config.HOTKEY_MODE == "hold":
        combo = _resolve_hold_combo()
        held: set = set()
        combo_down = False  # tracks intent, not recorder state — race-free with the worker queue

        def on_press(key):
            nonlocal combo_down
            held.add(key)
            if not combo_down and all(group & held for group in combo):
                combo_down = True
                _actions.put("start")

        def on_release(key):
            nonlocal combo_down
            held.discard(key)
            if combo_down and any(key in group for group in combo):
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
