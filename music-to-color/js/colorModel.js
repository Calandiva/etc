/* 주파수 ↔ 색채 매핑 수학 — python/music_to_color/color_model.py 와 동일 로직.
   소리 주파수(Hz) 구간을 사용자가 고른 스케일(linear/log/exp)로 가시광선 주파수(THz) 구간에
   연결하고, 격자 칸 하나당 고정 주파수대 하나를 미리 배정해 위치가 흔들리지 않게 한다. */
(function (g) {
  const EXP_BASE = 9; // log 의 반대 모양(저음 압축·고음 확장) 곡률 상수. normalizeT/valueFromT 가 서로 역함수.
  const C_NM_THZ = 299792.458; // 빛의 속도: nm·THz 단위에서 wavelength_nm = C / freq_THz
  const VISIBLE_NM = [380, 780];

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // value(min..max 구간의 값) -> t(0..1), scale 모양대로 정규화
  function normalizeT(value, min, max, scale) {
    const raw = clamp((value - min) / (max - min), 0, 1);
    if (scale === "log") return clamp(Math.log(value / min) / Math.log(max / min), 0, 1);
    if (scale === "exp") return clamp((Math.pow(EXP_BASE, raw) - 1) / (EXP_BASE - 1), 0, 1);
    return raw;
  }

  // t(0..1) -> value(min..max 구간), normalizeT 의 역함수
  function valueFromT(t, min, max, scale) {
    t = clamp(t, 0, 1);
    if (scale === "log") return min * Math.pow(max / min, t);
    if (scale === "exp") {
      const raw = Math.log(t * (EXP_BASE - 1) + 1) / Math.log(EXP_BASE);
      return min + raw * (max - min);
    }
    return min + t * (max - min);
  }

  const thzToNm = (freqTHz) => C_NM_THZ / freqTHz;

  // 파장(nm) -> RGB 0..255. 가시광선 근사식(Dan Bruton 알고리즘).
  function wavelengthToRGB(wlRaw) {
    const wl = clamp(wlRaw, VISIBLE_NM[0], VISIBLE_NM[1]);
    let r = 0, gg = 0, b = 0;
    if (wl < 440) { r = -(wl - 440) / (440 - 380); b = 1; }
    else if (wl < 490) { gg = (wl - 440) / (490 - 440); b = 1; }
    else if (wl < 510) { gg = 1; b = -(wl - 510) / (510 - 490); }
    else if (wl < 580) { r = (wl - 510) / (580 - 510); gg = 1; }
    else if (wl < 645) { r = 1; gg = -(wl - 645) / (645 - 580); }
    else { r = 1; }
    let factor;
    if (wl < 420) factor = 0.3 + 0.7 * (wl - 380) / (420 - 380);
    else if (wl < 701) factor = 1;
    else factor = 0.3 + 0.7 * (780 - wl) / (780 - 700);
    const gamma = 0.8;
    const adjust = (c) => (c <= 0 ? 0 : Math.pow(c * factor, gamma));
    return [Math.round(255 * adjust(r)), Math.round(255 * adjust(gg)), Math.round(255 * adjust(b))];
  }

  // 격자 N x N 전체의 고정 밴드 목록을 미리 계산한다: 칸 하나 = 소리 주파수대 하나 = 고정 색.
  function buildBands({ soundMin, soundMax, lightMin, lightMax, scale, gridSize }) {
    const n = gridSize, bands = new Array(n * n);
    for (let i = 0; i < n * n; i++) {
      const t = (i + 0.5) / (n * n);
      const freqHz = valueFromT(t, soundMin, soundMax, scale);
      const lightTHz = lightMin + t * (lightMax - lightMin);
      bands[i] = {
        row: Math.floor(i / n),
        col: i % n,
        freqHz,
        lightTHz,
        color: wavelengthToRGB(thzToNm(lightTHz)),
      };
    }
    return bands;
  }

  g.ColorModel = { normalizeT, valueFromT, thzToNm, wavelengthToRGB, buildBands, EXP_BASE, VISIBLE_NM };
})(window);
