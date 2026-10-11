"""주파수 ↔ 색채 매핑 수학 — js/colorModel.js 와 동일 로직.

소리 주파수(Hz) 구간을 사용자가 고른 스케일(linear/log/exp)로 가시광선 주파수(THz) 구간에
연결하고, 격자 N x N 전체의 고정 밴드(칸 하나 = 고정 주파수대 하나 = 고정 색)를 만든다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EXP_BASE = 9  # log 의 반대 모양(저음 압축·고음 확장) 곡률 상수. normalize_t/value_from_t 가 서로 역함수.
C_NM_THZ = 299792.458  # 빛의 속도: nm·THz 단위에서 wavelength_nm = C / freq_THz
VISIBLE_NM = (380.0, 780.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def normalize_t(value: float, vmin: float, vmax: float, scale: str) -> float:
    """value(vmin..vmax 구간의 값) -> t(0..1), scale 모양대로 정규화."""
    raw = _clamp((value - vmin) / (vmax - vmin), 0.0, 1.0)
    if scale == "log":
        return _clamp(math.log(value / vmin) / math.log(vmax / vmin), 0.0, 1.0)
    if scale == "exp":
        return _clamp((EXP_BASE ** raw - 1) / (EXP_BASE - 1), 0.0, 1.0)
    return raw


def value_from_t(t: float, vmin: float, vmax: float, scale: str) -> float:
    """t(0..1) -> value(vmin..vmax 구간), normalize_t 의 역함수."""
    t = _clamp(t, 0.0, 1.0)
    if scale == "log":
        return vmin * (vmax / vmin) ** t
    if scale == "exp":
        raw = math.log(t * (EXP_BASE - 1) + 1) / math.log(EXP_BASE)
        return vmin + raw * (vmax - vmin)
    return vmin + t * (vmax - vmin)


def thz_to_nm(freq_thz: float) -> float:
    return C_NM_THZ / freq_thz


def wavelength_to_rgb(wavelength_nm: float) -> tuple[int, int, int]:
    """파장(nm) -> RGB 0..255. 가시광선 근사식(Dan Bruton 알고리즘)."""
    wl = _clamp(wavelength_nm, *VISIBLE_NM)
    r = g = b = 0.0
    if wl < 440:
        r, b = -(wl - 440) / (440 - 380), 1.0
    elif wl < 490:
        g, b = (wl - 440) / (490 - 440), 1.0
    elif wl < 510:
        g, b = 1.0, -(wl - 510) / (510 - 490)
    elif wl < 580:
        r, g = (wl - 510) / (580 - 510), 1.0
    elif wl < 645:
        r, g = 1.0, -(wl - 645) / (645 - 580)
    else:
        r = 1.0

    if wl < 420:
        factor = 0.3 + 0.7 * (wl - 380) / (420 - 380)
    elif wl < 701:
        factor = 1.0
    else:
        factor = 0.3 + 0.7 * (780 - wl) / (780 - 700)

    gamma = 0.8

    def adjust(c: float) -> int:
        return round(255 * ((c * factor) ** gamma)) if c > 0 else 0

    return adjust(r), adjust(g), adjust(b)


@dataclass(frozen=True)
class Band:
    row: int
    col: int
    freq_hz: float
    light_thz: float
    color: tuple[int, int, int]


def build_bands(
    sound_min: float,
    sound_max: float,
    light_min: float,
    light_max: float,
    scale: str,
    grid_size: int,
) -> list[Band]:
    """격자 N x N 전체의 고정 밴드 목록을 미리 계산한다: 칸 하나 = 소리 주파수대 하나 = 고정 색."""
    n = grid_size
    total = n * n
    bands: list[Band] = []
    for i in range(total):
        t = (i + 0.5) / total
        freq_hz = value_from_t(t, sound_min, sound_max, scale)
        light_thz = light_min + t * (light_max - light_min)
        color = wavelength_to_rgb(thz_to_nm(light_thz))
        bands.append(Band(row=i // n, col=i % n, freq_hz=freq_hz, light_thz=light_thz, color=color))
    return bands
