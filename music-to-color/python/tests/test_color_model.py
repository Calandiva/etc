import math

import pytest

from music_to_color.color_model import (
    build_chroma_bands,
    build_sweep_bands,
    normalize_t,
    note_frequency,
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
def test_build_sweep_bands_shape_and_bounds(scale, grid_size):
    layout = build_sweep_bands(100.0, 4000.0, 405.0, 790.0, scale, grid_size)
    bands = layout.bands
    assert layout.cols == grid_size and layout.rows == grid_size
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


def test_build_sweep_bands_reversed_light_range_still_monotonic_colors():
    # 고음을 저주파 빛(빨강)에, 저음을 고주파 빛(보라)에 매칭하도록 뒤집어도 깨지지 않아야 한다.
    layout = build_sweep_bands(100.0, 4000.0, 790.0, 405.0, "linear", 16)
    assert len(layout.bands) == 256
    assert layout.bands[0].light_thz > layout.bands[-1].light_thz


def test_note_frequency_known_values():
    # A4 = 440Hz(라), C4 = 261.63Hz(도) 가 표준 평균율 기준값과 일치해야 한다.
    assert note_frequency(4, 5) == pytest.approx(440.0, abs=0.01)  # 라4
    assert note_frequency(4, 0) == pytest.approx(261.63, abs=0.01)  # 도4
    assert note_frequency(3, 5) == pytest.approx(220.0, abs=0.01)  # 라3 (한 옥타브 아래 = 절반)


def test_build_chroma_bands_layout_is_7_columns_by_octaves():
    layout = build_chroma_bands(100.0, 4000.0, 405.0, 790.0, "log")
    assert layout.cols == 7
    assert len(layout.bands) == layout.cols * layout.rows

    cols = {b.col for b in layout.bands}
    assert cols == set(range(7))

    # 모든 밴드 주파수가 음계상 올바른 반음에 정확히(평균율) 위치해야 한다
    for b in layout.bands:
        midi = 69 + 12 * math.log2(b.freq_hz / 440.0)
        assert abs(midi - round(midi)) < 1e-6


def test_build_chroma_bands_higher_octave_renders_toward_row_zero():
    layout = build_chroma_bands(100.0, 4000.0, 405.0, 790.0, "log")
    # 같은 열(도)에서 이미지 row가 작을수록(위쪽) 주파수가 더 높아야 한다 ("높은 음을 위로 쌓기")
    do_band_by_row = {b.row: b.freq_hz for b in layout.bands if b.col == 0}
    rows_sorted = sorted(do_band_by_row)
    freqs_sorted_by_row = [do_band_by_row[r] for r in rows_sorted]
    assert freqs_sorted_by_row == sorted(freqs_sorted_by_row, reverse=True)
