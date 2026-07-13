"""System tray icon and menu for Prose."""

import os

import pystray

import config
import startup
from icon import make_glyph

# Windows draws the tray at 16px (24px at 150% DPI). Render at 32 and let the
# shell downscale — the glyph geometry is designed to survive it.
_TRAY_PX = 32

_TOOLTIPS = {
    "idle": "Prose — ready",
    "listening": "Prose — listening...",
    "processing": "Prose — transcribing...",
    "disabled": "Prose — disabled",
}


class Tray:
    """Owns the pystray icon; exposes set_state() and runs on the calling thread."""

    def __init__(self, app_state, on_quit):
        """
        app_state: object with .enabled and .cleanup_enabled booleans (mutated by menu)
        on_quit:   callback invoked when the user picks Quit
        """
        self._app_state = app_state
        self._on_quit = on_quit

        menu = pystray.Menu(
            pystray.MenuItem(
                "Enabled",
                self._toggle_enabled,
                checked=lambda item: self._app_state.enabled,
            ),
            pystray.MenuItem(
                "AI Cleanup",
                self._toggle_cleanup,
                checked=lambda item: self._app_state.cleanup_enabled,
            ),
            pystray.MenuItem(
                "Start with Windows",
                self._toggle_startup,
                checked=lambda item: startup.is_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open settings folder", self._open_config_dir),
            pystray.MenuItem("Quit", self._quit),
        )
        self.icon = pystray.Icon(
            "Prose",
            icon=make_glyph(_TRAY_PX, "idle"),
            title=_TOOLTIPS["idle"],
            menu=menu,
        )

    # --- menu handlers ---

    def _toggle_enabled(self, icon, item):
        self._app_state.enabled = not self._app_state.enabled
        self.set_state("idle" if self._app_state.enabled else "disabled")

    def _toggle_cleanup(self, icon, item):
        self._app_state.cleanup_enabled = not self._app_state.cleanup_enabled

    def _toggle_startup(self, icon, item):
        try:
            on = startup.toggle()
            print(f"[startup] start with Windows: {'ON' if on else 'off'}")
        except OSError as e:
            print(f"[startup] could not update: {e}")

    def _open_config_dir(self, icon, item):
        """Open %APPDATA%\\Prose so the user can edit or delete their saved keys."""
        try:
            config.USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(config.USER_CONFIG_DIR)
        except OSError as e:
            print(f"[tray] could not open config folder: {e}")

    def _quit(self, icon, item):
        self.icon.stop()
        self._on_quit()

    # --- public API ---

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        self.icon.icon = make_glyph(_TRAY_PX, state)
        self.icon.title = tooltip or _TOOLTIPS.get(state, "Prose")

    def run(self, setup=None) -> None:
        """Blocks the calling thread until Quit. `setup` runs once the icon is visible."""
        self.icon.run(setup=setup)
