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


def _handle_skip(reason: str | None) -> None:
    """A recording produced no usable audio. A too-short tap is normal and silent;
    a full-length recording with no sound means a dead/muted/busy mic — tell the user."""
    if reason == "silent":
        mic = getattr(recorder, "device_name", None) or "your microphone"
        stats = getattr(recorder, "last_stats", None) or "no measurements"
        print(
            f"[recording] no audio from '{mic}' ({stats}) - "
            f"mic may be muted or in use by another app"
        )
        if tray is not None:
            tray.notify(
                f"No audio from “{mic}”.\nCheck it isn't muted or in use by another app, "
                f"or pick a different mic from the tray → Microphone.",
                "Prose — no sound detected",
            )
    else:
        print(f"[recording] {reason or 'empty'} - ignored")


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
                    try:
                        recorder.start()
                    except Exception as e:
                        _print_error(e)
                        mic = config.MIC_DEVICE or "the default microphone"
                        if tray is not None:
                            tray.notify(
                                f"Couldn't open “{mic}”.\nPick a different one from the "
                                f"tray → Microphone.",
                                "Prose — microphone error",
                            )
                        _set_status("idle" if state.enabled else "disabled")
                        continue
                    _set_status("listening")
                    print("[recording] started")
            elif action == "stop":
                if recorder.is_recording:
                    wav = recorder.stop()
                    print("[recording] stopped")
                    if wav is None:
                        _handle_skip(recorder.last_reason)
                        _set_status("idle" if state.enabled else "disabled")
                    else:
                        threading.Thread(target=_process, args=(wav,), daemon=True).start()
        except Exception as e:
            _print_error(e)
            _set_status("idle", "Prose - error (see console)")


# --- hotkey handling (callbacks only enqueue; all work happens on the worker) ---


def _toggle_recording() -> None:
    _actions.put("stop" if recorder.is_recording else "start")


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
# The hook reports side-specific modifier VKs (VK_LCONTROL, not VK_CONTROL); map
# them to the generic VK so an event can be matched against a generic combo group.
_GENERIC = {0xA2: 0x11, 0xA3: 0x11, 0xA0: 0x10, 0xA1: 0x10, 0xA4: 0x12, 0xA5: 0x12}

# Low-level keyboard-hook message types.
_WM_DOWN = (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
_WM_UP = (0x0101, 0x0105)    # WM_KEYUP, WM_SYSKEYUP
_LLKHF_INJECTED = 0x10       # KBDLLHOOKSTRUCT.flags bit: event came from SendInput

_user32 = ctypes.windll.user32
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
_user32.VkKeyScanW.restype = ctypes.c_short


def _key_is_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _resolve_hold_vks() -> list[tuple[int, ...]]:
    """Parse HOLD_KEY ('ctrl+win', 'f9', ...) into one VK group per combo part.

    The combo is down when every group has at least one of its keys down.
    """
    groups = []
    for part in config.HOLD_KEY.split("+"):
        part = part.strip()
        if part in _VK:
            groups.append(_VK[part])
        elif len(part) > 1 and part[0] == "f" and part[1:].isdigit():
            groups.append((0x70 + int(part[1:]) - 1,))  # VK_F1..VK_F24
        elif len(part) == 1:
            groups.append((_user32.VkKeyScanW(part) & 0xFF,))
        else:
            raise ValueError(f"Unknown key in HOLD_KEY: {part!r}")
    return groups


def _start_hotkey_listener():
    global _listener
    if config.HOTKEY_MODE == "hold":
        groups = _resolve_hold_vks()
        non_win = tuple(vk for g in groups for vk in g if vk not in _WIN_VKS)
        has_win = any(vk in _WIN_VKS for g in groups for vk in g)
        suppressed_wins: set[int] = set()
        st = {"down": False}  # our intent; recorder state is the worker's business

        def combo_complete(changed_vk: int, changed_down: bool) -> bool:
            """Is every part of the combo down? The hook fires before Windows updates
            the async key state, so trust the event for the key that just changed and
            read the OS (GetAsyncKeyState) for the rest — this avoids a missed key-up
            leaving a key stuck 'down' and letting a lone Ctrl fire the hotkey.
            The event's vkCode is side-specific (VK_LCONTROL, never VK_CONTROL), so
            match it against its group in generic form too — otherwise a Ctrl event
            never counts as 'the key that just changed' and, with Win pressed first,
            start/stop wait on a stale async read instead of firing immediately."""
            changed = {changed_vk, _GENERIC.get(changed_vk, changed_vk)}
            for g in groups:
                if changed & set(g):
                    if changed_down:
                        continue  # this group is satisfied by the key that just went down
                    if not any(vk not in changed and _key_is_down(vk) for vk in g):
                        return False  # the key went up and no sibling is down
                elif not any(_key_is_down(vk) for vk in g):
                    return False
            return True

        # Everything runs inside the low-level filter. Suppressing the Win key (so
        # Windows never opens the Start menu / search) also stops pynput's
        # on_press/on_release from firing for it, so the combo logic can't be split
        # across both. Consuming Win here — synchronously, in the hook, before the OS
        # sees it — is what finally makes Ctrl+Win reliable: no Start menu, no stray
        # paste into the search box.
        def filt(msg, data):
            vk = data.vkCode
            down, up = msg in _WM_DOWN, msg in _WM_UP
            if not (down or up):
                return True

            if _injecting.is_set():
                # While inject() synthesises Ctrl+V, skip only the injected events
                # themselves; physical keys must still reach the Win bookkeeping
                # below, or suppressed_wins desyncs from what the OS actually saw.
                if data.flags & _LLKHF_INJECTED:
                    return True
            elif down and not st["down"] and combo_complete(vk, True):
                st["down"] = True
                _actions.put("start")
            elif up and st["down"] and not combo_complete(vk, False):
                st["down"] = False
                _actions.put("stop")

            # Hide Win from the OS when it joins the combo — and hide it all-or-
            # nothing: auto-repeat downs and the final up must follow whatever the
            # initial key-down did. A half-hidden Win (down seen, up swallowed)
            # leaves Windows convinced the key is held forever, turning every later
            # keystroke into a Win+<key> shortcut (our Ctrl+V paste became
            # Win+Ctrl+V, the volume flyout). GetAsyncKeyState still reports the
            # *previous* state inside the hook, so on a down event it tells an
            # initial press (False) from an auto-repeat the OS already saw (True).
            if has_win and vk in _WIN_VKS:
                if down:
                    if vk in suppressed_wins:
                        _listener.suppress_event()  # repeat of a press we swallowed
                    elif not _key_is_down(vk) and any(_key_is_down(v) for v in non_win):
                        suppressed_wins.add(vk)  # initial press joining the combo
                        _listener.suppress_event()
                    # else: the OS already saw this Win go down (pressed before the
                    # rest of the combo, or alone) — leave its repeats and its up
                    # visible too; the intervening Ctrl keeps the Start menu shut.
                elif up and vk in suppressed_wins:
                    suppressed_wins.discard(vk)
                    _listener.suppress_event()
            return True

        _listener = keyboard.Listener(
            on_press=lambda k: None, on_release=lambda k: None, win32_event_filter=filt
        )
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
