"""Microphone capture — record while active, return an in-memory WAV."""

import io
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

import config


class Recorder:
    """Start/stop microphone recording; stop() returns WAV bytes (or None if too short)."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self.level = 0.0  # live RMS of the latest chunk — read by the overlay

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._chunks = []

        def callback(indata, frames, time_info, status):
            with self._lock:
                self._chunks.append(indata.copy())
            self.level = float(np.sqrt(np.mean(np.square(indata))))

        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            dtype="float32",
            callback=callback,
        )
        self._stream.start()

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
            return None
        audio = np.concatenate(chunks)
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_RECORD_SECONDS:
            return None  # accidental tap — nothing worth transcribing
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < config.SILENCE_RMS:
            return None  # effectively silent — Whisper would hallucinate text

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
