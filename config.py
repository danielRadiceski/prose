"""Configuration for Prose — loads .env and exposes settings."""

import os
import sys
from pathlib import Path

APP_VERSION = "1.0.1"
PUBLISHER = "Daniel Radicheski"  # embedded in the exe's file properties

import truststore
from dotenv import load_dotenv

# Use the Windows certificate store for TLS verification. Required when antivirus
# (e.g. Avast Web Shield) intercepts HTTPS and re-signs certs with its own root,
# which Python's bundled CA list doesn't trust.
truststore.inject_into_ssl()

# Where the first-run dialog stores a user's keys. Under %APPDATA% rather than
# beside the exe, so it works when Prose is installed to Program Files.
USER_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Prose"
USER_ENV_PATH = USER_CONFIG_DIR / ".env"

# First loaded wins (load_dotenv never overwrites an existing value):
#   1. .env beside Prose.exe  -> portable install
#   2. .env in the project    -> development
#   3. %APPDATA%\Prose\.env   -> normal user install
if getattr(sys, "frozen", False):
    load_dotenv(Path(sys.executable).parent / ".env")
load_dotenv()
load_dotenv(USER_ENV_PATH)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- API keys ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- Transcription (always Groq Whisper) ---
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3-turbo")

# --- Cleanup provider: groq | anthropic | gemini ---
# groq is the default: it reuses the transcription key (no second signup) and
# returns in ~270ms vs ~1.3s for Claude Haiku.
CLEANUP_PROVIDER = os.getenv("CLEANUP_PROVIDER", "groq").strip().lower()
GROQ_CLEANUP_MODEL = os.getenv("GROQ_CLEANUP_MODEL", "llama-3.3-70b-versatile")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# --- Hotkey ---
# "toggle": press once to start recording, press again to stop.
# "hold":   push-to-talk — record while HOLD_KEY is held down.
# HOLD_KEY accepts a single key ("f9") or a combo ("ctrl+win"). Avoid bare "alt" —
# releasing Alt activates the menu bar in many apps, which swallows the paste.
HOTKEY_MODE = os.getenv("HOTKEY_MODE", "toggle").strip().lower()
TOGGLE_HOTKEY = os.getenv("TOGGLE_HOTKEY", "<ctrl>+<alt>+<space>")
HOLD_KEY = os.getenv("HOLD_KEY", "ctrl+win").strip().lower()

# --- Behavior ---
CLEANUP_ENABLED = _env_bool("CLEANUP_ENABLED", True)
OVERLAY_ENABLED = _env_bool("OVERLAY_ENABLED", True)  # floating waveform pill

# --- Audio ---
SAMPLE_RATE = 16000  # Whisper's native rate; keeps uploads small
CHANNELS = 1
MIN_RECORD_SECONDS = 0.4  # ignore accidental key taps shorter than this
SILENCE_RMS = 0.004  # RMS amplitude below this = silence, skip transcription
# Which microphone to record from: a device-name substring, or "" for the Windows
# default. Set from the tray (Microphone menu); some laptops' default mic is dead,
# so this lets you pin Prose to one that works.
MIC_DEVICE = os.getenv("MIC_DEVICE", "")


def set_user_setting(key: str, value: str) -> None:
    """Persist one setting to %APPDATA%\\Prose\\.env, leaving the rest intact."""
    from dotenv import dotenv_values

    values = dict(dotenv_values(USER_ENV_PATH)) if USER_ENV_PATH.exists() else {}
    values[key] = value
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in values.items() if v is not None]
    USER_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_mic_device(name: str) -> None:
    """Choose the recording mic (name substring, or '' for system default)."""
    global MIC_DEVICE
    MIC_DEVICE = name or ""
    set_user_setting("MIC_DEVICE", MIC_DEVICE)


def reload_keys() -> None:
    """Re-read keys after the setup dialog has written them.

    transcribe/cleanup read these off the module at call time, so updating the
    globals here is enough — no restart needed.
    """
    global GROQ_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, CLEANUP_ENABLED
    load_dotenv(USER_ENV_PATH, override=True)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    CLEANUP_ENABLED = _env_bool("CLEANUP_ENABLED", True)
