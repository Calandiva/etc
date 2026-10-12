(function () {
  const { ColorModel } = window;
  const engine = new window.AudioEngine();

  const el = (id) => document.getElementById(id);
  const canvas = el("canvas"), stage = el("stage"), panel = el("panel");
  const visualizer = new window.Visualizer(canvas, stage);

  const ui = {
    fileInput: el("fileInput"),
    playBtn: el("playBtn"),
    visualMode: el("visualMode"),
    soundMin: el("soundMin"), soundMax: el("soundMax"),
    lightMin: el("lightMin"), lightMax: el("lightMax"),
    scaleSelect: el("scaleSelect"),
    resolutionSection: el("resolutionSection"),
    resolutionLabel: el("resolutionLabel"),
    resolution: el("resolution"), resolutionVal: el("resolutionVal"),
    modeInfo: el("modeInfo"),
    smoothing: el("smoothing"), minBrightness: el("minBrightness"),
    harmonicSuppress: el("harmonicSuppress"),
    pixelated: el("pixelated"),
    fullscreenBtn: el("fullscreenBtn"),
    panelToggle: el("panelToggle"),
    status: el("status"), timeDisplay: el("timeDisplay"),
    gridWarning: el("gridWarning"),
  };

  // 격자형(주파수)·방사형·직선형·채색 악보만 "해상도"를 직접 고른다. 음계형/사이클/관계형은
  // 음 이름 수(12)와 옥타브 수로 칸 수가 자동으로 정해져서 슬라이더가 필요 없다.
  const RESOLUTION_CONFIG = {
    sweep: { min: 10, max: 1000, default: 32, label: "격자 한 변 칸 수 (칸 수 = 값²)" },
    radial: { min: 24, max: 360, default: 180, label: "분할 수 (부드러움)" },
    linear: { min: 8, max: 200, default: 48, label: "막대 수" },
    score: { min: 4, max: 40, default: 14, label: "줄 수 (전체 곡을 몇 줄로 접을지)" },
  };
  // 채색 악보: 한 줄 = SCORE_COLS개 시간 구간, 구간마다 SCORE_FREQ_BINS개 주파수를 위아래로 쌓는다
  const SCORE_COLS = 140, SCORE_FREQ_BINS = 30;

  const state = {
    bands: [], raw: new Float32Array(0), smoothed: new Float32Array(0), frame: 0,
    staticMode: false, score: null, scoreGen: 0,
  };

  function clampNum(v, lo, hi, dflt) {
    v = parseFloat(v);
    if (!isFinite(v)) return dflt;
    return Math.min(hi, Math.max(lo, v));
  }

  function fmtTime(t) {
    if (!isFinite(t)) return "0:00";
    const m = Math.floor(t / 60), sec = Math.floor(t % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function readSettings() {
    return {
      visualMode: ui.visualMode.value,
      soundMin: clampNum(ui.soundMin.value, 20, 19999, 100),
      soundMax: clampNum(ui.soundMax.value, 21, 20000, 4000),
      lightMin: clampNum(ui.lightMin.value, 50, 2000, 405),
      lightMax: clampNum(ui.lightMax.value, 50, 2000, 790),
      scale: ui.scaleSelect.value,
    };
  }

  // 불러온 곡 전체를 한 번 분석해서 "접은 스펙트로그램" 하나로 고정 렌더링한다(음악을 하나의
  // 고정된 색면으로). 왼쪽 위부터 오른쪽으로 시간이 흐르고, 한 줄이 끝나면 다음 줄로 넘어간다.
  // 줄 안에서는 주파수가 아래(저음)->위(고음)로 쌓인다.
  async function rebuildScore(s, rowsFold) {
    const myGen = ++state.scoreGen;
    state.staticMode = true;

    if (!engine.buffer) {
      state.bands = [];
      visualizer.clear();
      ui.modeInfo.textContent = "먼저 음악 파일을 올리거나 샘플을 재생해보세요.";
      return;
    }

    ui.modeInfo.textContent = "전체 곡 분석 중...";
    const freqBands = ColorModel.buildFrequencyStrip({ ...s, count: SCORE_FREQ_BINS }).bands;
    const cols = SCORE_COLS;
    const totalSlices = cols * rowsFold;
    const harmonicSuppress = clampNum(ui.harmonicSuppress.value, 0, 1, 0.7);

    const sliceLevels = await engine.analyzeBuffer(freqBands, { totalSlices, harmonicSuppress });
    if (myGen !== state.scoreGen) return; // 그 사이 설정이 또 바뀜 — 낡은 결과는 버린다

    const F = freqBands.length;
    const gridRows = rowsFold * F;
    const bands = new Array(cols * gridRows);
    const levels = new Float32Array(cols * gridRows);
    for (let foldRow = 0; foldRow < rowsFold; foldRow++) {
      for (let t = 0; t < cols; t++) {
        const sliceLevel = sliceLevels[foldRow * cols + t];
        for (let f = 0; f < F; f++) {
          const imageRow = foldRow * F + (F - 1 - f); // 높은 주파수가 위로
          const idx = imageRow * cols + t;
          bands[idx] = { row: imageRow, col: t, color: freqBands[f].color };
          levels[idx] = sliceLevel[f];
        }
      }
    }

    state.bands = bands;
    state.smoothed = levels;
    state.score = { cols, rowsFold, freqBins: F, hopSeconds: engine.buffer.duration / totalSlices, totalSlices };
    visualizer.setLayout({ kind: "grid", cols, rows: gridRows });
    ui.modeInfo.textContent = `${rowsFold}줄 x ${cols}칸 (전체 ${totalSlices.toLocaleString()}구간, 곡 길이 ${fmtTime(engine.buffer.duration)}) 분석 완료`;
  }

  function rebuildBands() {
    const s = readSettings();
    if (s.soundMin >= s.soundMax) s.soundMax = s.soundMin + 1;
    const mode = s.visualMode;
    const resConfig = RESOLUTION_CONFIG[mode];

    if (resConfig) {
      ui.resolutionSection.style.display = "block";
      ui.resolutionLabel.textContent = resConfig.label;
      ui.resolution.min = resConfig.min;
      ui.resolution.max = resConfig.max;
      if (ui.resolution.dataset.mode !== mode) {
        ui.resolution.value = resConfig.default;
        ui.resolution.dataset.mode = mode;
      }
    } else {
      ui.resolutionSection.style.display = "none";
    }
    const resolution = Math.round(
      clampNum(ui.resolution.value, resConfig ? resConfig.min : 1, resConfig ? resConfig.max : 1000, resConfig ? resConfig.default : 32)
    );

    if (mode === "score") {
      state.scoreGen++; // 아직 안 끝난 이전 분석은 이제 낡은 것으로 취급
      ui.resolutionVal.textContent = `${resolution}줄`;
      ui.gridWarning.style.display = "none";
      ui.modeInfo.style.display = "block";
      rebuildScore(s, resolution); // 비동기 — 끝나면 알아서 state.bands/smoothed를 채운다
      return;
    }
    state.staticMode = false;
    state.score = null;

    let bands, cols, rows, kind, infoText = "";
    if (mode === "chroma") {
      ({ bands, cols, rows } = ColorModel.buildChromaBands(s));
      kind = "grid";
      infoText = `가로 ${cols}칸(반음 포함 도~시) x 세로 ${rows}옥타브 (${bands.length}칸)`;
    } else if (mode === "sweep") {
      ({ bands, cols, rows } = ColorModel.buildSweepBands({ ...s, gridSize: resolution }));
      kind = "grid";
    } else if (mode === "radial") {
      ({ bands, cols, rows } = ColorModel.buildFrequencyStrip({ ...s, count: resolution }));
      kind = "radial";
    } else if (mode === "linear") {
      ({ bands, cols, rows } = ColorModel.buildFrequencyStrip({ ...s, count: resolution }));
      kind = "linear";
    } else if (mode === "cycle") {
      const chromatic = ColorModel.buildPitchClassNodes(s);
      bands = ColorModel.FIFTHS_ORDER.map((idx) => chromatic[idx]);
      kind = "cycle";
      infoText = "5도권 순서로 12음을 원에 고정 배치합니다. 소리가 커지면 그 음이 밝아집니다.";
    } else {
      bands = ColorModel.buildPitchClassNodes(s);
      kind = "relational";
      infoText = "반음 순서로 12음을 원에 배치하고, 동시에 울리는 음끼리 선으로 잇습니다.";
    }

    state.bands = bands;
    state.raw = new Float32Array(bands.length);
    state.smoothed = new Float32Array(bands.length);
    visualizer.setLayout({ kind, cols, rows });

    ui.modeInfo.style.display = resConfig ? "none" : "block";
    if (infoText) ui.modeInfo.textContent = infoText;

    if (mode === "sweep") {
      ui.resolutionVal.textContent = `${resolution} x ${resolution} (${(resolution * resolution).toLocaleString()}칸)`;
      ui.gridWarning.style.display = resolution * resolution > 250000 ? "block" : "none";
    } else if (resConfig) {
      ui.resolutionVal.textContent = `밴드 ${resolution}개`;
      ui.gridWarning.style.display = "none";
    } else {
      ui.gridWarning.style.display = "none";
    }
  }

  function loop() {
    requestAnimationFrame(loop);

    if (state.staticMode) {
      const minBrightness = clampNum(ui.minBrightness.value, 0, 0.5, 0.08);
      if (state.bands.length) {
        visualizer.render(state.smoothed, state.bands, minBrightness);
        if (state.score && engine.playing) {
          const t = Math.min(engine.currentTime, engine.duration - 1e-4);
          const sliceIndex = Math.min(state.score.totalSlices - 1, Math.floor(t / state.score.hopSeconds));
          const foldRow = Math.floor(sliceIndex / state.score.cols);
          const col = sliceIndex % state.score.cols;
          visualizer.markPlayhead(col, foldRow * state.score.freqBins, state.score.freqBins);
        }
      }
      if (engine.buffer) {
        ui.timeDisplay.textContent = `${fmtTime(engine.currentTime)} / ${fmtTime(engine.duration)}`;
        if (!engine.playing && engine.offset === 0 && ui.playBtn.textContent !== "재생") ui.playBtn.textContent = "재생";
      }
      return;
    }

    const n = state.bands.length;
    if (!n) return;
    const skip = n > 600000 ? 3 : n > 250000 ? 2 : 1;
    state.frame++;
    if (state.frame % skip !== 0) return;

    const harmonicSuppress = clampNum(ui.harmonicSuppress.value, 0, 1, 0.7);
    if (engine.playing) engine.getBandLevels(state.bands, state.raw, harmonicSuppress);
    else state.raw.fill(0);

    const alpha = clampNum(ui.smoothing.value, 0, 0.97, 0.7);
    const minBrightness = clampNum(ui.minBrightness.value, 0, 0.5, 0.08);
    for (let i = 0; i < n; i++) {
      state.smoothed[i] = state.smoothed[i] * alpha + state.raw[i] * (1 - alpha);
    }
    visualizer.render(state.smoothed, state.bands, minBrightness);

    if (engine.buffer) {
      ui.timeDisplay.textContent = `${fmtTime(engine.currentTime)} / ${fmtTime(engine.duration)}`;
      if (!engine.playing && engine.offset === 0 && ui.playBtn.textContent !== "재생") {
        ui.playBtn.textContent = "재생";
      }
    }
  }

  ["visualMode", "soundMin", "soundMax", "lightMin", "lightMax", "scaleSelect", "resolution"].forEach((id) => {
    ui[id].addEventListener("change", rebuildBands);
  });
  ui.resolution.addEventListener("input", () => {
    // 채색 악보는 "줄 수"를 바꿀 때마다 곡 전체를 다시 분석해야 해서 무겁다 — 드래그 중엔
    // 라벨만 갱신하고, 실제 재분석은 손을 뗄 때(change)만 한다.
    if (ui.visualMode.value === "score") {
      ui.resolutionVal.textContent = `${ui.resolution.value}줄 (손을 떼면 다시 분석)`;
      return;
    }
    rebuildBands();
  });
  ui.harmonicSuppress.addEventListener("change", () => {
    if (ui.visualMode.value === "score") rebuildBands();
  });

  ui.pixelated.addEventListener("change", () => visualizer.setPixelated(ui.pixelated.checked));

  ui.fileInput.addEventListener("change", async (e) => {
    engine.unlock(); // 제스처(change) 안에서 바로: await 뒤로 미루면 자동재생 잠금이 안 풀린다
    const file = e.target.files[0];
    if (!file) return;
    ui.status.textContent = "불러오는 중...";
    try {
      await engine.loadFile(file);
      ui.status.textContent = `${file.name}`;
      ui.playBtn.disabled = false;
      ui.playBtn.textContent = "재생";
      rebuildBands(); // 채색 악보 모드라면 새로 불러온 곡으로 다시 분석
    } catch (err) {
      ui.status.textContent = "오디오 디코딩 실패: " + err.message;
    }
  });

  document.querySelectorAll(".sampleBtn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      engine.unlock(); // 제스처(click) 안에서 바로: await 뒤로 미루면 자동재생 잠금이 안 풀린다
      document.querySelectorAll(".sampleBtn").forEach((b) => (b.disabled = true));
      ui.status.textContent = `${btn.dataset.name} 불러오는 중...`;
      try {
        const res = await fetch(btn.dataset.src);
        const arrayBuffer = await res.arrayBuffer();
        await engine.loadArrayBuffer(arrayBuffer);
        ui.status.textContent = `샘플: ${btn.dataset.name}`;
        ui.playBtn.disabled = false;
        rebuildBands(); // 채색 악보 모드라면 새로 불러온 곡으로 다시 분석
        engine.play();
        ui.playBtn.textContent = "일시정지";
      } catch (err) {
        ui.status.textContent = "샘플 불러오기 실패: " + err.message;
      } finally {
        document.querySelectorAll(".sampleBtn").forEach((b) => (b.disabled = false));
      }
    });
  });

  ui.playBtn.addEventListener("click", () => {
    engine.unlock(); // 제스처(click) 안에서 바로
    if (engine.playing) {
      engine.pause();
      ui.playBtn.textContent = "재생";
    } else {
      engine.play();
      ui.playBtn.textContent = "일시정지";
    }
  });

  ui.fullscreenBtn.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else stage.requestFullscreen();
  });

  ui.panelToggle.addEventListener("click", () => panel.classList.toggle("collapsed"));

  rebuildBands();
  ui.playBtn.disabled = true;
  requestAnimationFrame(loop);
})();
