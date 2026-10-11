"""index.html에서 바로 재생해 볼 수 있는 합성 샘플 음원 4개를 music-to-color/samples/ 에 만든다.
저작권 문제 없는 순수 합성 사인파/노이즈만 쓴다. 실행: python -m music_to_color.make_samples
"""
from __future__ import annotations

import os
import struct
import wave

import numpy as np

SR = 44100
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "samples")


def envelope(n: int, attack: int, release: int) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    a = min(attack, n // 2)
    r = min(release, n // 2)
    if a:
        env[:a] = np.linspace(0, 1, a, dtype=np.float32)
    if r:
        env[-r:] = np.linspace(1, 0, r, dtype=np.float32)
    return env


def sine(freq: float, dur: float, amp: float = 1.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(dur * sr), dtype=np.float32) / sr
    n = len(t)
    return amp * np.sin(2 * np.pi * freq * t) * envelope(n, int(0.01 * sr), int(0.05 * sr))


def write_wav(path: str, samples: np.ndarray, sr: int = SR) -> None:
    peak = np.max(np.abs(samples)) or 1.0
    samples = samples / peak * 0.9
    pcm = (samples * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def make_sweep() -> np.ndarray:
    """100Hz -> 3500Hz 로그 스윕 8초. 단일 음이 격자 전체를 빨강->보라로 훑는 모습을 보여준다."""
    dur = 8.0
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    f0, f1 = 100.0, 3500.0
    k = (f1 / f0) ** (1.0 / dur)
    phase = 2 * np.pi * f0 * (k ** t - 1) / np.log(k)
    sig = np.sin(phase).astype(np.float32) * envelope(n, int(0.05 * SR), int(0.3 * SR))
    return sig


def make_arpeggio() -> np.ndarray:
    """3옥타브 장음계 아르페지오를 두 번 왕복. 음이 하나씩 다른 밴드를 켠다."""
    notes_hz = [130.81, 164.81, 196.00, 261.63, 329.63, 392.00, 523.25, 659.25, 784.00, 1046.50]
    seq = notes_hz + notes_hz[::-1]
    note_dur = 0.3
    chunks = [sine(f, note_dur, amp=0.8) for f in seq]
    return np.concatenate(chunks)


def make_rhythm_beat() -> np.ndarray:
    """120bpm 킥(저음)+하이햇(고음) 루프 4초(8비트). 저음·고음 두 색이 번갈아 깜빡인다."""
    bpm = 120
    beat = 60.0 / bpm
    bars = 8
    n = int(bars * beat * SR)
    out = np.zeros(n, dtype=np.float32)

    def place(sig: np.ndarray, start_s: float) -> None:
        s = int(start_s * SR)
        e = min(n, s + len(sig))
        out[s:e] += sig[: e - s]

    kick = sine(60, 0.18, amp=1.0)
    kick *= np.exp(-np.arange(len(kick)) / (0.05 * SR))

    rng = np.random.default_rng(42)
    hat_len = int(0.06 * SR)
    hat = rng.standard_normal(hat_len).astype(np.float32)
    hat_t = np.arange(hat_len) / SR
    hat *= np.sin(2 * np.pi * 3200 * hat_t) * np.exp(-hat_t / 0.02)

    for bar in range(bars):
        base = bar * beat
        place(kick, base)
        place(kick, base + beat * 0.5)
        place(hat * 0.5, base + beat * 0.25)
        place(hat * 0.5, base + beat * 0.75)
    return out


def make_sustained_chord() -> np.ndarray:
    """저음~고음에 걸친 6개 음을 동시에 6초간 지속. 화음=여러 색이 한꺼번에 켜지는 모습을 보여준다."""
    freqs = [110.0, 220.0, 277.18, 329.63, 554.37, 880.0]
    dur = 6.0
    chord = np.zeros(int(dur * SR), dtype=np.float32)
    for f in freqs:
        chord += sine(f, dur, amp=1.0 / len(freqs))
    t = np.arange(len(chord)) / SR
    tremolo = 1.0 + 0.15 * np.sin(2 * np.pi * 2.5 * t)
    return (chord * tremolo).astype(np.float32)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    samples = {
        "sweep.wav": make_sweep(),
        "arpeggio.wav": make_arpeggio(),
        "rhythm_beat.wav": make_rhythm_beat(),
        "sustained_chord.wav": make_sustained_chord(),
    }
    for name, sig in samples.items():
        path = os.path.join(OUT_DIR, name)
        write_wav(path, sig)
        print(f"{name}: {len(sig) / SR:.1f}s -> {path}")


if __name__ == "__main__":
    main()
