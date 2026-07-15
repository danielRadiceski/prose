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


def _short(name: str, n: int = 38) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


class Tray:
    """Owns the pystray icon; exposes set_state()/notify() and runs on the calling thread."""

    def __init__(self, app_state, on_quit):
        """
        app_state: object with .enabled and .cleanup_enabled booleans (mutated by menu)
        on_quit:   callback invoked when the user picks Quit
        """
        self._app_state = app_state
        self._on_quit = on_quit
        self.icon = pystray.Icon(
            "Prose",
            icon=make_glyph(_TRAY_PX, "idle"),
            title=_TOOLTIPS["idle"],
            menu=self._build_menu(),
        )

    # --- menu construction (rebuilt on rescan so the device list stays current) ---

    def _build_menu(self) -> "pystray.Menu":
        return pystray.Menu(
            pystray.MenuItem(
                "Enabled", self._toggle_enabled,
                checked=lambda item: self._app_state.enabled,
            ),
            pystray.MenuItem(
                "AI Cleanup", self._toggle_cleanup,
                checked=lambda item: self._app_state.cleanup_enabled,
            ),
            pystray.MenuItem("Microphone", pystray.Menu(*self._mic_items())),
            pystray.MenuItem(
                "Start with Windows", self._toggle_startup,
                checked=lambda item: startup.is_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open settings folder", self._open_config_dir),
            pystray.MenuItem("Quit", self._quit),
        )

    def _mic_items(self) -> list:
        from audio import list_input_devices

        items = [
            pystray.MenuItem(
                "System default (auto)",
                lambda icon, item: self._pick_mic(""),
                radio=True,
                checked=lambda item: not config.MIC_DEVICE,
            )
        ]
        try:
            devices = list_input_devices()
        except Exception as e:
            print(f"[tray] could not list microphones: {e}")
            devices = []
        for _idx, name, is_default in devices:
            label = _short(name) + ("  (Windows default)" if is_default else "")
            items.append(
                pystray.MenuItem(
                    label,
                    (lambda n: lambda icon, item: self._pick_mic(n))(name),
                    radio=True,
                    checked=(lambda n: lambda item: config.MIC_DEVICE == n)(name),
                )
            )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("↻ Rescan devices", self._rescan_mics))
        return items

    def _refresh_menu(self) -> None:
        self.icon.menu = self._build_menu()
        self.icon.update_menu()

    # --- menu handlers ---

    def _toggle_enabled(self, icon, item):
        self._app_state.enabled = not self._app_state.enabled
        self.set_state("idle" if self._app_state.enabled else "disabled")

    def _toggle_cleanup(self, icon, item):
        self._app_state.cleanup_enabled = not self._app_state.cleanup_enabled

    def _pick_mic(self, name: str):
        config.set_mic_device(name)
        pretty = _short(name) if name else "System default"
        print(f"[mic] recording device set to: {pretty}")
        self.notify(f"Microphone: {pretty}", "Prose")
        self.icon.update_menu()  # refresh the radio dots

    def _rescan_mics(self, icon, item):
        from audio import refresh_devices

        refresh_devices()  # re-enumerate hardware, not just rebuild from the cache
        self._refresh_menu()

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

    def notify(self, message: str, title: str = "Prose") -> None:
        """Show a Windows notification balloon. Best-effort — never raises."""
        try:
            self.icon.notify(message, title)
        except Exception as e:
            print(f"[tray] notify failed: {e}")

    def run(self, setup=None) -> None:
        """Blocks the calling thread until Quit. `setup` runs once the icon is visible."""
        self.icon.run(setup=setup)
