/* 주파수 ↔ 색채 매핑 수학 — python/music_to_color/color_model.py 와 동일 로직.
   소리 주파수(Hz) 구간을 사용자가 고른 스케일(linear/log/exp)로 가시광선 주파수(THz) 구간에
   연결한다. 격자 배치는 두 가지: "스윕형"(N x N, 소리 구간 전체를 균등 분할)과
   "음계형"(가로 7칸 도레미파솔라시, 세로 옥타브) — 둘 다 칸마다 고정 주파수·고정 색을 가진다. */
(function (g) {
  const EXP_BASE = 9; // log 의 반대 모양(저음 압축·고음 확장) 곡률 상수. normalizeT/valueFromT 가 서로 역함수.
  const C_NM_THZ = 299792.458; // 빛의 속도: nm·THz 단위에서 wavelength_nm = C / freq_THz
  const VISIBLE_NM = [380, 780];
  const DEGREE_SEMITONES = [0, 2, 4, 5, 7, 9, 11]; // 도 레 미 파 솔 라 시 (C 기준 반음 수)
  const DEGREE_NAMES = ["도", "레", "미", "파", "솔", "라", "시"];

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

  function colorFor(freqHz, soundMin, soundMax, lightMin, lightMax, scale) {
    const t = normalizeT(freqHz, soundMin, soundMax, scale);
    const lightTHz = lightMin + t * (lightMax - lightMin);
    return { lightTHz, color: wavelengthToRGB(thzToNm(lightTHz)) };
  }

  // 스윕형: 격자 N x N 전체의 고정 밴드. 칸 하나 = 소리 주파수 구간을 균등분할한 주파수대 하나.
  function buildSweepBands({ soundMin, soundMax, lightMin, lightMax, scale, gridSize }) {
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
    return { bands, cols: n, rows: n };
  }

  // 평균율 음 하나의 주파수. octave: 과학적 음높이 표기(C4=미디 60) · degreeIndex: 0~6 (도~시)
  function noteFrequency(octave, degreeIndex) {
    const midi = (octave + 1) * 12 + DEGREE_SEMITONES[degreeIndex];
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  const nearestMidi = (freqHz) => Math.round(69 + 12 * Math.log2(freqHz / 440));
  const octaveOfMidi = (midi) => Math.floor(midi / 12) - 1;

  // 음계형: 가로 7칸(도레미파솔라시) x 세로 옥타브 수. 칸 하나 = 특정 옥타브의 특정 음 하나.
  // 높은 옥타브가 위로 쌓이도록 행을 뒤집어 배치한다.
  function buildChromaBands({ soundMin, soundMax, lightMin, lightMax, scale }) {
    const octMin = octaveOfMidi(nearestMidi(soundMin));
    const octMax = Math.max(octMin, octaveOfMidi(nearestMidi(soundMax)));
    const rows = octMax - octMin + 1, cols = DEGREE_NAMES.length;
    const bands = [];
    for (let r = 0; r < rows; r++) {
      const octave = octMin + r;
      const imageRow = rows - 1 - r;
      for (let c = 0; c < cols; c++) {
        const freqHz = noteFrequency(octave, c);
        const { lightTHz, color } = colorFor(freqHz, soundMin, soundMax, lightMin, lightMax, scale);
        bands.push({ row: imageRow, col: c, freqHz, lightTHz, color, label: `${DEGREE_NAMES[c]}${octave}` });
      }
    }
    return { bands, cols, rows };
  }

  function buildLayout(settings) {
    return settings.layout === "sweep" ? buildSweepBands(settings) : buildChromaBands(settings);
  }

  g.ColorModel = {
    normalizeT, valueFromT, thzToNm, wavelengthToRGB,
    buildSweepBands, buildChromaBands, buildLayout,
    noteFrequency, DEGREE_NAMES,
    EXP_BASE, VISIBLE_NM,
  };
})(window);
