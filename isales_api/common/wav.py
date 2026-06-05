"""PCM → WAV wrapping for browser-playable audio.

The TTS providers yield raw PCM (16-bit LE mono); browsers cannot play bare
PCM via ``<audio>``. ``pcm16_to_wav`` prepends a RIFF/WAVE container so the
greeting 试听 endpoint can return ``audio/wav`` (campaign-greeting-tts-preview
§ 决策 2). Uses the stdlib ``wave`` module rather than hand-packing the 44-byte
header so the chunk sizes stay correct.
"""

from __future__ import annotations

import io
import wave


def pcm16_to_wav(pcm: bytes, *, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap 16-bit little-endian PCM in a WAV container and return the bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
