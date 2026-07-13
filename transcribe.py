"""Speech-to-text via Groq's Whisper endpoint (OpenAI-compatible)."""

import requests

import config


def transcribe(wav_bytes: bytes) -> str:
    """Send WAV audio to Groq Whisper and return the raw transcript text."""
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set — add it to .env")

    resp = requests.post(
        config.GROQ_TRANSCRIBE_URL,
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model": config.GROQ_MODEL,
            "response_format": "json",
            "language": "en",
            "temperature": "0",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


if __name__ == "__main__":
    # Standalone test: transcribe test.wav (create one with `py audio.py`)
    with open("test.wav", "rb") as f:
        print(transcribe(f.read()))
