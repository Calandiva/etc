(function () {
  const { ColorModel } = window;
  const engine = new window.AudioEngine();

  const el = (id) => document.getElementById(id);
  const canvas = el("canvas"), stage = el("stage"), panel = el("panel");
  const visualizer = new window.Visualizer(canvas, stage);

  const ui = {
    fileInput: el("fileInput"),
    playBtn: el("playBtn"),
    layoutSelect: el("layoutSelect"),
    soundMin: el("soundMin"), soundMax: el("soundMax"),
    lightMin: el("lightMin"), lightMax: el("lightMax"),
    scaleSelect: el("scaleSelect"),
    gridSizeSection: el("gridSizeSection"),
    gridSize: el("gridSize"), gridSizeVal: el("gridSizeVal"),
    chromaInfo: el("chromaInfo"),
    smoothing: el("smoothing"), minBrightness: el("minBrightness"),
    harmonicSuppress: el("harmonicSuppress"),
    pixelated: el("pixelated"),
    fullscreenBtn: el("fullscreenBtn"),
    panelToggle: el("panelToggle"),
    status: el("status"), timeDisplay: el("timeDisplay"),
    gridWarning: el("gridWarning"),
  };

  const state = { bands: [], raw: new Float32Array(0), smoothed: new Float32Array(0), frame: 0 };

  function clampNum(v, lo, hi, dflt) {
    v = parseFloat(v);
    if (!isFinite(v)) return dflt;
    return Math.min(hi, Math.max(lo, v));
  }

  function readSettings() {
    return {
      layout: ui.layoutSelect.value,
      soundMin: clampNum(ui.soundMin.value, 20, 19999, 100),
      soundMax: clampNum(ui.soundMax.value, 21, 20000, 4000),
      lightMin: clampNum(ui.lightMin.value, 50, 2000, 405),
      lightMax: clampNum(ui.lightMax.value, 50, 2000, 790),
      scale: ui.scaleSelect.value,
      gridSize: Math.round(clampNum(ui.gridSize.value, 10, 1000, 32)),
    };
  }

  function rebuildBands() {
    const s = readSettings();
    if (s.soundMin >= s.soundMax) s.soundMax = s.soundMin + 1;

    const isChroma = s.layout === "chroma";
    ui.gridSizeSection.style.display = isChroma ? "none" : "block";
    ui.chromaInfo.style.display = isChroma ? "block" : "none";

    const { bands, cols, rows } = ColorModel.buildLayout(s);
    state.bands = bands;
    state.raw = new Float32Array(bands.length);
    state.smoothed = new Float32Array(bands.length);
    visualizer.setGridDims(cols, rows);

    if (isChroma) {
      ui.chromaInfo.textContent = `가로 ${cols}칸(도레미파솔라시) x 세로 ${rows}옥타브 (${bands.length}칸)`;
    } else {
      ui.gridSizeVal.textContent = `${s.gridSize} x ${s.gridSize} (${(s.gridSize * s.gridSize).toLocaleString()}칸)`;
      ui.gridWarning.style.display = s.gridSize * s.gridSize > 250000 ? "block" : "none";
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

  ["layoutSelect", "soundMin", "soundMax", "lightMin", "lightMax", "scaleSelect", "gridSize"].forEach((id) => {
    ui[id].addEventListener("change", rebuildBands);
  });
  ui.gridSize.addEventListener("input", rebuildBands);

  ui.pixelated.addEventListener("change", () => visualizer.setPixelated(ui.pixelated.checked));

  ui.fileInput.addEventListener("change", async (e) => {
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
