"""오프라인 음악→색채 분석기. 브라우저 앱(js/)과 같은 밴드 매핑·기음/배음 보정 로직을
WAV 파일에 직접 적용해, 프레임별 밴드 밝기를 JSON으로 뽑거나(모든 환경에서 동작) 미리보기
영상을 만든다(imageio가 있을 때만).

사용 예:
  python -m music_to_color.audio_render --input song.wav --output-json out.json \
      --layout chroma --sound-min 100 --sound-max 4000 --scale log
"""
from __future__ import annotations

import argparse
import json
import wave
from typing import Iterator

import numpy as np

from .color_model import Band, Layout, build_layout


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """WAV 파일을 모노 float32(-1..1) 샘플과 샘플레이트로 읽는다."""
    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sample_width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(
            f"지원하지 않는 샘플 폭입니다({sample_width * 8}bit). 8/16/32bit PCM WAV만 지원합니다."
        )

    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)
    return data, sample_rate


def _emphasize_fundamentals(db: np.ndarray, bin_hz: float, min_db: float, max_db: float, strength: float) -> np.ndarray:
    """스펙트럼 피크를 찾아 기음/배음을 분류하고, 배음 피크 주변을 어둡게 깎은 보정 스펙트럼을
    돌려준다(js/audioEngine.js의 _emphasizeFundamentals와 동일 로직). strength 0 = 보정 없음."""
    adjusted = db.copy()
    if strength <= 0:
        return adjusted

    span = max_db - min_db
    threshold = min_db + 0.12 * span
    min_separation_hz = 20.0

    n = len(db)
    above = np.nonzero(db >= threshold)[0]
    above = above[(above > 0) & (above < n - 1)]
    is_peak = (db[above] > db[above - 1]) & (db[above] >= db[above + 1])
    peak_idx = above[is_peak]
    if len(peak_idx) == 0:
        return adjusted

    order = np.argsort(-db[peak_idx])
    peak_idx = peak_idx[order]
    peak_freq = peak_idx * bin_hz
    peak_db = db[peak_idx]

    kept_idx: list[int] = []
    kept_freq: list[float] = []
    for i, f in zip(peak_idx, peak_freq):
        if all(abs(f - kf) >= min_separation_hz for kf in kept_freq):
            kept_idx.append(i)
            kept_freq.append(f)

    fundamentals: list[float] = []
    window = 2
    darken_db = 36.0 * strength
    for i, f in zip(kept_idx, kept_freq):
        harmonic_order = 0
        for ff in fundamentals:
            ratio = f / ff
            k = round(ratio)
            if 2 <= k <= 16 and abs(ratio - k) / k <= 0.03:
                harmonic_order = k
                break
        if harmonic_order > 0:
            reduce = darken_db * min(1.5, 0.6 + 0.08 * harmonic_order)
            lo, hi = max(0, i - window), min(n - 1, i + window)
            adjusted[lo : hi + 1] -= reduce
        else:
            fundamentals.append(f)
    return adjusted


def iter_band_levels(
    samples: np.ndarray,
    sample_rate: int,
    bands: list[Band],
    fft_size: int = 2048,
    hop_size: int | None = None,
    min_db: float = -90.0,
    max_db: float = -10.0,
    harmonic_suppress: float = 0.7,
) -> Iterator[np.ndarray]:
    """프레임마다 밴드별 0..1 레벨을 계산한다. 기음/배음 보정 후, 각 밴드 주파수에서 가장
    가까운 두 FFT bin 사이를 선형보간한다(js/audioEngine.js의 getBandLevels와 동일)."""
    hop_size = hop_size or fft_size // 4
    window = np.hanning(fft_size).astype(np.float32)
    window_sum = float(window.sum())
    n_bins = fft_size // 2 + 1
    bin_hz = sample_rate / fft_size
    freqs_hz = np.array([b.freq_hz for b in bands], dtype=np.float64)
    bin_pos = freqs_hz * fft_size / sample_rate
    lo = np.clip(np.floor(bin_pos).astype(int), 0, n_bins - 1)
    hi = np.clip(lo + 1, 0, n_bins - 1)
    frac = bin_pos - lo

    pos = 0
    n = len(samples)
    while pos + fft_size <= n:
        frame = samples[pos : pos + fft_size] * window
        spectrum = np.fft.rfft(frame)
        mag = np.abs(spectrum) * 2.0 / window_sum
        db = 20 * np.log10(mag + 1e-9)
        db = _emphasize_fundamentals(db, bin_hz, min_db, max_db, harmonic_suppress)
        interp_db = db[lo] + (db[hi] - db[lo]) * frac
        level = np.clip((interp_db - min_db) / (max_db - min_db), 0.0, 1.0)
        yield level
        pos += hop_size


def render_frames_to_video(frames: list[np.ndarray], bands: list[Band], cols: int, rows: int, min_brightness: float, fps: float, output_path: str) -> None:
    try:
        import imageio.v2 as imageio
    except ImportError as e:
        raise RuntimeError(
            "영상 출력에는 imageio(+ffmpeg 플러그인)가 필요합니다: pip install imageio imageio-ffmpeg"
        ) from e

    colors = np.array([b.color for b in bands], dtype=np.float32)  # (len(bands), 3)
    rowcol = np.array([(b.row, b.col) for b in bands], dtype=np.int64)
    images = []
    for level in frames:
        brightness = min_brightness + (1 - min_brightness) * np.sqrt(np.clip(level, 0, 1))
        rgb = (colors * brightness[:, None]).astype(np.uint8)
        frame_img = np.zeros((rows, cols, 3), dtype=np.uint8)
        frame_img[rowcol[:, 0], rowcol[:, 1]] = rgb
        images.append(frame_img)
    imageio.mimwrite(output_path, images, fps=fps)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="입력 WAV 파일 경로")
    p.add_argument("--output-json", help="프레임별 밴드 레벨/고정 색을 저장할 JSON 경로")
    p.add_argument("--output-video", help="미리보기 영상 경로(mp4/gif, imageio 필요)")
    p.add_argument("--layout", choices=["chroma", "sweep"], default="chroma", help="격자 배치: 음계형(기본) 또는 스윕형")
    p.add_argument("--grid-size", type=int, default=32, help="스윕형일 때 격자 한 변 크기 (10~1000)")
    p.add_argument("--sound-min", type=float, default=100.0)
    p.add_argument("--sound-max", type=float, default=4000.0)
    p.add_argument("--light-min", type=float, default=405.0)
    p.add_argument("--light-max", type=float, default=790.0)
    p.add_argument("--scale", choices=["linear", "log", "exp"], default="log")
    p.add_argument("--fft-size", type=int, default=8192, help="js/audioEngine.js 기본값(8192)과 맞춤 — 작을수록 배음 분리가 거칠어진다")
    p.add_argument("--hop-size", type=int, default=None)
    p.add_argument("--min-db", type=float, default=-90.0)
    p.add_argument("--max-db", type=float, default=-10.0)
    p.add_argument("--smoothing", type=float, default=0.7, help="프레임 간 평활 계수 0..0.97")
    p.add_argument("--harmonic-suppress", type=float, default=0.7, help="기음 강조/배음 억제 강도 0..1")
    p.add_argument("--min-brightness", type=float, default=0.08)
    args = p.parse_args()

    grid_size = max(10, min(1000, args.grid_size))
    layout: Layout = build_layout(args.layout, args.sound_min, args.sound_max, args.light_min, args.light_max, args.scale, grid_size)
    bands = layout.bands

    samples, sample_rate = read_wav(args.input)
    hop_size = args.hop_size or args.fft_size // 4
    fps = sample_rate / hop_size

    smoothed = np.zeros(len(bands), dtype=np.float64)
    frames: list[np.ndarray] = []
    for level in iter_band_levels(
        samples, sample_rate, bands, args.fft_size, hop_size, args.min_db, args.max_db, args.harmonic_suppress
    ):
        smoothed = smoothed * args.smoothing + level * (1 - args.smoothing)
        frames.append(smoothed.copy())

    print(f"{len(frames)} 프레임 분석 완료 (fps={fps:.2f}, 밴드={len(bands)}, 격자={layout.cols}x{layout.rows})")

    if args.output_json:
        payload = {
            "sample_rate": sample_rate,
            "fps": fps,
            "cols": layout.cols,
            "rows": layout.rows,
            "bands": [
                {"row": b.row, "col": b.col, "freq_hz": b.freq_hz, "light_thz": b.light_thz, "color": list(b.color), "label": b.label}
                for b in bands
            ],
            "frames": [[round(float(x), 4) for x in frame] for frame in frames],
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"JSON 저장: {args.output_json}")

    if args.output_video:
        render_frames_to_video(frames, bands, layout.cols, layout.rows, args.min_brightness, fps, args.output_video)
        print(f"영상 저장: {args.output_video}")


if __name__ == "__main__":
    main()
