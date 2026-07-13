"""Run Prose automatically when Windows starts.

Uses the per-user Run key (HKCU) rather than a Startup-folder shortcut: no admin
rights, no COM, and Windows surfaces it under Settings → Apps → Startup where the
user can toggle it independently.
"""

import sys
import winreg
from pathlib import Path

APP_NAME = "Prose"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """The exact command Windows should run at logon (quoted for spaces in paths)."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    # Dev mode: pythonw.exe runs main.py without a console window.
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    return f'"{interpreter}" "{Path(__file__).parent / "main.py"}"'


def _open(access):
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, access)


def is_enabled() -> bool:
    """True if a Run entry exists AND still points at this build of Prose."""
    try:
        with _open(winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False
    return value == launch_command()


def enable(command: str | None = None) -> None:
    """Register at logon. `command` defaults to however this process was started."""
    with _open(winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command or launch_command())


def disable() -> None:
    try:
        with _open(winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass  # already off


def toggle() -> bool:
    """Flip the setting; returns the new state."""
    if is_enabled():
        disable()
        return False
    enable()
    return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "enable":
        enable()
        print(f"Enabled. Windows will run: {launch_command()}")
    elif action == "disable":
        disable()
        print("Disabled.")
    elif action == "status":
        print(f"start with Windows: {'ON' if is_enabled() else 'off'}")
        print(f"command would be:   {launch_command()}")
    else:
        sys.exit("usage: py startup.py [status|enable|disable]")
