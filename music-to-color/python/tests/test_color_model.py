import math

import pytest

from music_to_color.color_model import (
    build_bands,
    normalize_t,
    thz_to_nm,
    value_from_t,
    wavelength_to_rgb,
)

SCALES = ["linear", "log", "exp"]


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("t", [0.0, 0.1, 0.37, 0.5, 0.82, 1.0])
def test_value_from_t_normalize_t_are_inverses(scale, t):
    value = value_from_t(t, 100.0, 4000.0, scale)
    assert 100.0 - 1e-6 <= value <= 4000.0 + 1e-6
    assert normalize_t(value, 100.0, 4000.0, scale) == pytest.approx(t, abs=1e-6)


@pytest.mark.parametrize("scale", SCALES)
def test_value_from_t_endpoints(scale):
    assert value_from_t(0.0, 100.0, 4000.0, scale) == pytest.approx(100.0, abs=1e-6)
    assert value_from_t(1.0, 100.0, 4000.0, scale) == pytest.approx(4000.0, abs=1e-6)


def test_thz_to_nm_round_trip():
    nm = thz_to_nm(500.0)
    assert nm == pytest.approx(299792.458 / 500.0)


@pytest.mark.parametrize(
    "wavelength_nm,dominant",
    [
        (650, "r"),  # 적색
        (510, "g"),  # 녹색 경계부
        (560, "g"),  # 녹색
        (450, "b"),  # 청색
    ],
)
def test_wavelength_to_rgb_dominant_channel(wavelength_nm, dominant):
    r, g, b = wavelength_to_rgb(wavelength_nm)
    channels = {"r": r, "g": g, "b": b}
    assert channels[dominant] == max(r, g, b)
    assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255


def test_wavelength_to_rgb_clamps_outside_visible_range():
    assert wavelength_to_rgb(100) == wavelength_to_rgb(380)
    assert wavelength_to_rgb(5000) == wavelength_to_rgb(780)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("grid_size", [10, 32, 100])
def test_build_bands_shape_and_bounds(scale, grid_size):
    bands = build_bands(100.0, 4000.0, 405.0, 790.0, scale, grid_size)
    assert len(bands) == grid_size * grid_size

    rows = {b.row for b in bands}
    cols = {b.col for b in bands}
    assert rows == set(range(grid_size))
    assert cols == set(range(grid_size))

    for b in bands:
        assert 100.0 <= b.freq_hz <= 4000.0
        assert 405.0 <= b.light_thz <= 790.0
        assert all(0 <= c <= 255 for c in b.color)

    # 밴드 인덱스가 증가하면 주파수도 단조 증가(세 스케일 모두 valueFromT가 단조함수)
    freqs = [b.freq_hz for b in bands]
    assert all(freqs[i] <= freqs[i + 1] for i in range(len(freqs) - 1))


def test_build_bands_reversed_light_range_still_monotonic_colors():
    # 고음을 저주파 빛(빨강)에, 저음을 고주파 빛(보라)에 매칭하도록 뒤집어도 깨지지 않아야 한다.
    bands = build_bands(100.0, 4000.0, 790.0, 405.0, "linear", 16)
    assert len(bands) == 256
    assert bands[0].light_thz > bands[-1].light_thz
