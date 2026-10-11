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

  // 격자형(주파수)·방사형·직선형만 "해상도"를 직접 고른다. 음계형/사이클/관계형은 음 이름 수(12)와
  // 옥타브 수로 칸 수가 자동으로 정해져서 슬라이더가 필요 없다.
  const RESOLUTION_CONFIG = {
    sweep: { min: 10, max: 1000, default: 32, label: "격자 한 변 칸 수 (칸 수 = 값²)" },
    radial: { min: 24, max: 360, default: 180, label: "분할 수 (부드러움)" },
    linear: { min: 8, max: 200, default: 48, label: "막대 수" },
  };

  const state = { bands: [], raw: new Float32Array(0), smoothed: new Float32Array(0), frame: 0 };

  function clampNum(v, lo, hi, dflt) {
    v = parseFloat(v);
    if (!isFinite(v)) return dflt;
    return Math.min(hi, Math.max(lo, v));
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

  function fmtTime(t) {
    if (!isFinite(t)) return "0:00";
    const m = Math.floor(t / 60), sec = Math.floor(t % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function loop() {
    requestAnimationFrame(loop);
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
  ui.resolution.addEventListener("input", rebuildBands);

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
