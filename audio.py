"""Microphone capture — record while active, return an in-memory WAV."""

import io
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

import config


# Generic host-API aliases and loopback endpoints that aren't real microphones.
_NON_MIC = ("sound mapper", "primary sound capture", "stereo mix", "pc speaker",
            "wave out", "what u hear", "output")


def refresh_devices() -> None:
    """Re-enumerate audio hardware.

    PortAudio caches the device list AND the default device at initialisation, so
    a mic connected (or made the Windows default) after Prose launched is invisible
    — and 'system default' would resolve to the stale old default — until we
    reinitialise. Safe only when no stream is open, which is the case here.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        print(f"[audio] device refresh failed: {e}")


def list_input_devices() -> list[tuple[int, str, bool]]:
    """Real microphones as (index, name, is_system_default), one entry per device.

    The same physical mic appears once per audio subsystem (MME, DirectSound,
    WASAPI, WDM-KS). MME truncates the name to 31 chars and WDM-KS shows raw
    endpoints like 'Microphone Array 1 ()'. We collapse these by name prefix,
    keeping the fullest friendly name, and drop aliases/loopbacks.
    """
    try:
        default = sd.default.device[0]
    except Exception:
        default = -1
    by_key: dict[str, tuple[int, str, bool]] = {}
    for i, d in enumerate(sd.query_devices()):
        raw = d["name"]
        name = raw.strip()
        low = name.lower()
        if d["max_input_channels"] <= 0:
            continue
        if any(bad in low for bad in _NON_MIC) or name.endswith("()"):
            continue
        # Raw WDM-KS endpoints surface unresolved driver-resource strings like
        # 'Headset (@System32\\drivers\\bthhfenum.sys,#2;...)' — skip those; the same
        # device also has a friendly name ('Headset (Jabra Elite Active 75t)').
        if "@" in name or ".sys" in low or "\\drivers" in low or "\n" in raw or "\r" in raw:
            continue
        key = name[:30].lower()  # MME truncates at 31, so 30 chars collapses dupes
        is_default = i == default
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = (i, name, is_default)
        elif len(name) > len(cur[1]):  # prefer the fuller name; keep default flag
            by_key[key] = (i, name, is_default or cur[2])
        elif is_default:
            by_key[key] = (cur[0], cur[1], True)
    return sorted(by_key.values(), key=lambda t: t[0])


def resolve_device(selector):
    """Turn a MIC_DEVICE setting (device-name substring, or '') into a device index.

    Returns None to mean 'use the system default'. Matching by name rather than
    index keeps a chosen mic selected across reconnects/reboots, where indices shift.
    """
    if not selector:
        return None
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and str(selector).lower() in d["name"].lower():
            return i
    return None  # configured mic isn't present right now — fall back to default


class Recorder:
    """Start/stop microphone recording; stop() returns WAV bytes (or None if skipped)."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self._downmix = False
        self.level = 0.0  # live RMS of the latest chunk — read by the overlay
        self.last_reason = None  # why stop() returned None: 'short' | 'silent' | 'empty'
        self.device_name = None  # human-readable device actually opened

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        # indata is (frames, channels); collapse to mono when we had to open a
        # multi-channel device (some APIs refuse a 1-channel stream).
        data = indata.mean(axis=1, keepdims=True) if self._downmix else indata
        with self._lock:
            self._chunks.append(data.copy())
        self.level = float(np.sqrt(np.mean(np.square(data))))

    def start(self) -> None:
        if self._stream is not None:
            return
        self._chunks = []
        self.last_reason = None

        refresh_devices()  # pick up device/default changes since the last recording
        device = resolve_device(config.MIC_DEVICE)  # None -> system default
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self.device_name = info["name"]

        # Prefer a mono stream; if the device rejects it (WASAPI/WDM-KS often do),
        # reopen at its native channel count and average to mono in the callback.
        attempts = [(config.CHANNELS, False)]
        native = max(1, int(info["max_input_channels"]))
        if native > config.CHANNELS:
            attempts.append((native, True))
        last_err = None
        for channels, downmix in attempts:
            try:
                self._downmix = downmix
                self._stream = sd.InputStream(
                    device=device, samplerate=config.SAMPLE_RATE,
                    channels=channels, dtype="float32", callback=self._callback,
                )
                self._stream.start()
                return
            except sd.PortAudioError as e:
                self._stream = None
                last_err = e
        raise last_err

    def stop(self) -> bytes | None:
        if self._stream is None:
            return None
        self.level = 0.0
        self._stream.stop()
        self._stream.close()
        self._stream = None

        with self._lock:
            chunks, self._chunks = self._chunks, []

        if not chunks:
            self.last_reason = "empty"
            return None
        audio = np.concatenate(chunks)
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_RECORD_SECONDS:
            self.last_reason = "short"  # accidental tap — nothing worth transcribing
            return None
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < config.SILENCE_RMS:
            # Long enough to be a real attempt, but no sound arrived — a dead/muted
            # mic or one held by another app. The caller surfaces this to the user.
            self.last_reason = "silent"
            return None

        self.last_reason = None
        buf = io.BytesIO()
        sf.write(buf, audio, config.SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()


if __name__ == "__main__":
    # Standalone test: record 3 seconds and save to test.wav
    import time

    rec = Recorder()
    print("Recording 3 seconds... speak now")
    rec.start()
    time.sleep(3)
    wav = rec.stop()
    if wav:
        with open("test.wav", "wb") as f:
            f.write(wav)
        print(f"Saved test.wav ({len(wav)} bytes)")
    else:
        print("No audio captured")
