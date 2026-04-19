"""
Helper script to generate tests/fixtures/sample_meeting.wav.

Produces a 30-second 16 kHz mono PCM16 WAV containing alternating sine
bursts (1 s on / 1 s silence) to simulate speech-and-silence patterns.

Run once:
    python tests/fixtures/generate_sample_wav.py

The resulting file (~960 KB) is committed as a binary fixture.
This script is NOT run by pytest — it is documentation of how the fixture
was generated.
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000
DURATION_S = 30
FREQ_HZ = 440.0  # A4 — audible sine burst
BURST_DURATION_S = 1.0
SILENCE_DURATION_S = 1.0
AMPLITUDE = 16000  # PCM16 amplitude (max ~32767)

OUTPUT = Path(__file__).parent / "sample_meeting.wav"


def generate() -> None:
    n_samples = SAMPLE_RATE * DURATION_S
    samples: list[int] = []

    t = 0.0
    dt = 1.0 / SAMPLE_RATE
    in_burst = True
    segment_elapsed = 0.0
    segment_duration = BURST_DURATION_S

    for _ in range(n_samples):
        if in_burst:
            value = int(AMPLITUDE * math.sin(2.0 * math.pi * FREQ_HZ * t))
        else:
            value = 0

        samples.append(max(-32768, min(32767, value)))
        t += dt
        segment_elapsed += dt

        if segment_elapsed >= segment_duration:
            segment_elapsed = 0.0
            in_burst = not in_burst
            segment_duration = BURST_DURATION_S if in_burst else SILENCE_DURATION_S

    raw = struct.pack(f"<{n_samples}h", *samples)

    with wave.open(str(OUTPUT), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw)

    size_kb = OUTPUT.stat().st_size // 1024
    print(f"Written: {OUTPUT} ({size_kb} KB)")


if __name__ == "__main__":
    generate()
