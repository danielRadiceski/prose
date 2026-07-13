"""First-run setup: ask the user for their own API keys and store them.

Keys go to %APPDATA%\\Prose\\.env — per-user, no admin rights needed, and never
bundled into the exe. This is what makes Prose.exe safe to hand to someone else.
"""

import os
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

import config
from cleanup import PROVIDER_EXTRA_KEY

GROQ_URL = "https://console.groq.com/keys"

# Second key needed only when cleanup runs on a non-Groq provider.
_EXTRA_KEY_INFO = {
    "ANTHROPIC_API_KEY": ("Anthropic API key", "for AI cleanup", "https://console.anthropic.com/settings/keys"),
    "GEMINI_API_KEY": ("Gemini API key", "for AI cleanup", "https://aistudio.google.com/apikey"),
}

_BG = "#1e1e28"
_FG = "#e9e9f0"
_MUTED = "#9aa0b4"
_ACCENT = "#6b8afd"
_FIELD = "#2a2a38"


def extra_key_name() -> str | None:
    """The second API key this cleanup provider needs, if any."""
    return PROVIDER_EXTRA_KEY.get(config.CLEANUP_PROVIDER)


def missing_keys() -> list[str]:
    """Which required keys are absent, honouring provider and AI-cleanup setting."""
    missing = []
    if not config.GROQ_API_KEY:  # always needed: transcription runs on Groq Whisper
        missing.append("GROQ_API_KEY")
    extra = extra_key_name()
    if config.CLEANUP_ENABLED and extra and not getattr(config, extra, ""):
        missing.append(extra)
    return missing


def save_keys(groq: str, extra: str = "", cleanup: bool | None = None) -> None:
    """Write the keys to the per-user .env, preserving any other settings in it.

    `cleanup` is left untouched when None — AI cleanup is on by default and falls
    back to the raw transcript if the provider fails, so there's nothing to ask.
    """
    from dotenv import dotenv_values

    values = dict(dotenv_values(config.USER_ENV_PATH)) if config.USER_ENV_PATH.exists() else {}
    values["GROQ_API_KEY"] = groq.strip()
    if cleanup is not None:
        values["CLEANUP_ENABLED"] = "true" if cleanup else "false"
    extra_name = extra_key_name()
    if extra_name:
        values[extra_name] = extra.strip()

    config.USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in values.items() if v is not None]
    config.USER_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:  # best-effort: make it readable only by this user
        os.chmod(config.USER_ENV_PATH, 0o600)
    except OSError:
        pass


class _SetupDialog:
    def __init__(self, root: tk.Tk):
        self.saved = False
        self.root = root
        root.title("Prose — Setup")
        root.configure(bg=_BG)
        root.resizable(False, False)

        bold = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        body = tkfont.Font(family="Segoe UI", size=9)
        small = tkfont.Font(family="Segoe UI", size=8)

        pad = {"padx": 22}
        self.extra_name = extra_key_name()

        tk.Label(root, text="Welcome to Prose", font=bold, bg=_BG, fg=_FG).pack(
            anchor="w", pady=(20, 2), **pad
        )
        n, svc = (
            ("two API keys", "the services below")
            if self.extra_name
            else ("one free API key", "the service below")
        )
        tk.Label(
            root,
            text=f"Prose needs {n} of your own. Stored only on this PC, and never\n"
            f"sent anywhere except to {svc}.",
            font=body, bg=_BG, fg=_MUTED, justify="left",
        ).pack(anchor="w", pady=(0, 14), **pad)

        groq_hint = "speech-to-text" + ("" if self.extra_name else " and cleanup") + " — free tier"
        self.groq = self._key_field(root, "Groq API key", groq_hint, GROQ_URL, body, small, pad)

        self.extra = None
        if self.extra_name:
            label, hint, url = _EXTRA_KEY_INFO[self.extra_name]
            self.extra = self._key_field(root, label, hint, url, body, small, pad)

        tk.Label(
            root,
            text="Prose removes “um”s and fixes punctuation automatically. If that step\n"
            "ever fails, it pastes the plain transcript instead — nothing is lost.",
            font=small, bg=_BG, fg=_MUTED, justify="left",
        ).pack(anchor="w", pady=(2, 8), **pad)

        self.error = tk.Label(root, text="", font=small, bg=_BG, fg="#ff7b72")
        self.error.pack(anchor="w", pady=(0, 4), **pad)

        row = tk.Frame(root, bg=_BG)
        row.pack(fill="x", pady=(0, 18), **pad)
        tk.Button(
            row, text="Save and start", command=self._save, font=body,
            bg=_ACCENT, fg="white", activebackground="#5a78e8", activeforeground="white",
            relief="flat", padx=16, pady=6, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            row, text="Quit", command=root.destroy, font=body,
            bg=_FIELD, fg=_MUTED, activebackground="#343445", activeforeground=_FG,
            relief="flat", padx=14, pady=6, cursor="hand2",
        ).pack(side="right", padx=(0, 8))

        self._set_window_icon(root)

        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry(f"+{x}+{y}")
        root.bind("<Return>", lambda e: self._save())
        root.bind("<Escape>", lambda e: root.destroy())
        self.groq.focus_set()

    @staticmethod
    def _set_window_icon(root: tk.Tk) -> None:
        """Use the Prose glyph in the title bar and Alt-Tab, not Tk's feather."""
        try:
            from PIL import ImageTk

            from icon import make_glyph

            root._prose_icon = ImageTk.PhotoImage(make_glyph(64, "idle"))  # keep a ref
            root.iconphoto(True, root._prose_icon)
        except Exception:
            pass  # cosmetic only

    def _key_field(self, root, title, hint, url, body, small, pad) -> tk.Entry:
        tk.Label(root, text=title, font=body, bg=_BG, fg=_FG).pack(anchor="w", **pad)
        entry = tk.Entry(
            root, show="•", width=52, font=body, bg=_FIELD, fg=_FG,
            insertbackground=_FG, relief="flat", highlightthickness=1,
            highlightbackground="#3a3a4c", highlightcolor=_ACCENT,
        )
        entry.pack(anchor="w", ipady=5, pady=(3, 2), **pad)

        row = tk.Frame(root, bg=_BG)
        row.pack(anchor="w", pady=(0, 10), **pad)
        tk.Label(row, text=hint + "  ·", font=small, bg=_BG, fg=_MUTED).pack(side="left")
        link = tk.Label(row, text="get a key", font=small, bg=_BG, fg=_ACCENT, cursor="hand2")
        link.pack(side="left", padx=(4, 0))
        link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        return entry

    def _save(self):
        groq = self.groq.get().strip()
        extra = self.extra.get().strip() if self.extra is not None else ""

        if not groq:
            self.error.config(text="A Groq API key is required.")
            return
        if self.extra_name and not extra:
            label = _EXTRA_KEY_INFO[self.extra_name][0]
            self.error.config(text=f"{label} is required for CLEANUP_PROVIDER={config.CLEANUP_PROVIDER}.")
            return

        save_keys(groq, extra)
        self.saved = True
        self.root.destroy()


def prompt_for_keys() -> bool:
    """Show the setup dialog. Returns True if keys were saved, False if cancelled."""
    root = tk.Tk()
    dlg = _SetupDialog(root)
    root.mainloop()
    if dlg.saved:
        config.reload_keys()
    return dlg.saved


if __name__ == "__main__":
    print("saved" if prompt_for_keys() else "cancelled")
    print("config path:", config.USER_ENV_PATH)
