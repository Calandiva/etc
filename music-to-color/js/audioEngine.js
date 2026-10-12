/* Web Audio 재생 + FFT 분석. 밴드별 레벨은 "그 주파수에서 가장 가까운 두 FFT bin 사이 선형보간"으로
   구해서, 격자 칸 수(최대 1000x1000)가 FFT 해상도보다 훨씬 많아도 빈 칸 없이 매끈하게 채운다.

   실제 음원은 기음(기본주파수) 하나에 배음(정수배 倍音)이 줄줄이 따라붙어, 배음들도 꽤 크게
   잡히는 바람에 모든 대역이 한꺼번에 울렁거리는 문제가 있다. emphasizeFundamentals() 가 매
   프레임 스펙트럼 피크를 찾아, 가장 강한 피크(기음)는 그대로 두고 그 정수배 주파수에 걸리는
   약한 피크(배음)는 어둡게 깎아서, 실제로 울리는 음 각각이 또렷한 하나의 밝은 신호로 보이게
   한다. 이 보정 함수와 밴드 보간 함수는 순수 함수로 분리해서, 실시간 재생 경로와(아래)
   "채색 악보" 모드가 OfflineAudioContext로 곡 전체를 한 번에 분석하는 경로가 완전히
   같은 로직을 쓴다. */
(function (g) {
  const FFT_SIZE = 8192, MIN_DB = -90, MAX_DB = -10;

  // 스펙트럼(dB) 에서 피크를 찾아 기음/배음을 분류하고, 배음 피크 주변을 어둡게 깎은 보정
  // 스펙트럼을 돌려준다. strength 0 = 보정 없음(원본 그대로), 1 = 강하게 깎음.
  function emphasizeFundamentals(freqData, sampleRate, fftSize, minDb, maxDb, strength, scratch) {
    const n = freqData.length;
    const adjusted = scratch && scratch.length === n ? scratch : new Float32Array(n);
    adjusted.set(freqData);
    if (strength <= 0) return adjusted;

    const binHz = sampleRate / fftSize;
    const span = maxDb - minDb;
    const threshold = minDb + 0.12 * span;
    const minSeparationHz = 20;

    const peaks = [];
    for (let i = 1; i < n - 1; i++) {
      const v = freqData[i];
      if (v >= threshold && v > freqData[i - 1] && v >= freqData[i + 1]) peaks.push({ i, freq: i * binHz, db: v });
    }
    peaks.sort((a, b) => b.db - a.db);

    const kept = [];
    for (const p of peaks) {
      if (kept.every((k) => Math.abs(k.freq - p.freq) >= minSeparationHz)) kept.push(p);
    }

    const fundamentals = [];
    const window = 2; // 배음 피크의 FFT 로브 폭만큼 주변 bin도 같이 깎는다
    const darkenDb = 36 * strength;
    for (const p of kept) {
      let harmonicOrder = 0;
      for (const f of fundamentals) {
        const ratio = p.freq / f.freq;
        const k = Math.round(ratio);
        if (k >= 2 && k <= 16 && Math.abs(ratio - k) / k <= 0.03) { harmonicOrder = k; break; }
      }
      if (harmonicOrder > 0) {
        const reduce = darkenDb * Math.min(1.5, 0.6 + 0.08 * harmonicOrder);
        for (let j = Math.max(0, p.i - window); j <= Math.min(n - 1, p.i + window); j++) adjusted[j] -= reduce;
      } else {
        fundamentals.push(p);
      }
    }
    return adjusted;
  }

  // 보정된 스펙트럼(dB)에서, bands 각각의 고정 주파수 지점을 가장 가까운 두 FFT bin으로
  // 선형보간해 0..1 레벨을 구한다. out 에 결과를 채워 돌려준다.
  function interpolateBandLevels(spectrum, bands, sampleRate, fftSize, minDb, maxDb, out) {
    const n = spectrum.length, span = maxDb - minDb;
    for (let i = 0; i < bands.length; i++) {
      const binPos = (bands[i].freqHz * fftSize) / sampleRate;
      const lo = Math.max(0, Math.min(n - 1, Math.floor(binPos)));
      const hi = Math.max(0, Math.min(n - 1, lo + 1));
      const frac = binPos - lo;
      const db = spectrum[lo] + (spectrum[hi] - spectrum[lo]) * frac;
      out[i] = Math.max(0, Math.min(1, (db - minDb) / span));
    }
    return out;
  }

  class AudioEngine {
    constructor() {
      this.ctx = null;
      this.analyser = null;
      this.sourceNode = null;
      this.buffer = null;
      this.freqData = null;
      this.playing = false;
      this.startedAt = 0;
      this.offset = 0;
    }

    ensureContext() {
      if (!this.ctx) {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        this.analyser = this.ctx.createAnalyser();
        this.analyser.smoothingTimeConstant = 0; // 평활화는 app.js 가 밴드 단위로 직접 처리
        this.setFftSize(FFT_SIZE);
        this.setDecibelRange(MIN_DB, MAX_DB);
        this.analyser.connect(this.ctx.destination);
      }
      return this.ctx;
    }

    // 브라우저 자동재생 정책 때문에 AudioContext는 사용자 제스처(클릭) "안에서 직접" resume()을
    // 불러야 풀린다 — await 를 한 번이라도 거치고 나면(파일 디코딩 등) 같은 제스처로 안 쳐줘서
    // 재생 버튼을 눌러도 소리가 안 나고, 그다음 클릭(새 제스처)에서야 풀리는 문제가 있었다.
    // 그래서 파일 선택/샘플 버튼/재생 버튼 클릭 핸들러 맨 처음, await 전에 이걸 동기적으로 부른다.
    unlock() {
      const ctx = this.ensureContext();
      if (ctx.state !== "running") ctx.resume();
      return ctx;
    }

    setFftSize(size) {
      this.analyser.fftSize = size;
      this.freqData = new Float32Array(this.analyser.frequencyBinCount);
    }

    setDecibelRange(minDb, maxDb) {
      this.analyser.minDecibels = minDb;
      this.analyser.maxDecibels = maxDb;
      this.minDb = minDb;
      this.maxDb = maxDb;
    }

    async loadArrayBuffer(arrayBuffer) {
      const ctx = this.ensureContext();
      this.buffer = await ctx.decodeAudioData(arrayBuffer);
      this.stop();
    }

    async loadFile(file) {
      await this.loadArrayBuffer(await file.arrayBuffer());
    }

    play() {
      if (!this.buffer || this.playing) return;
      const ctx = this.ensureContext();
      if (ctx.state === "suspended") ctx.resume();
      const node = ctx.createBufferSource();
      node.buffer = this.buffer;
      node.connect(this.analyser);
      node.onended = () => {
        if (this.sourceNode === node) {
          this.playing = false;
          this.offset = 0;
          this.sourceNode = null;
        }
      };
      node.start(0, this.offset % this.buffer.duration);
      this.sourceNode = node;
      this.startedAt = ctx.currentTime - this.offset;
      this.playing = true;
    }

    pause() {
      if (!this.playing) return;
      this.offset = this.ctx.currentTime - this.startedAt;
      this.sourceNode.onended = null;
      this.sourceNode.stop();
      this.sourceNode = null;
      this.playing = false;
    }

    stop() {
      if (this.sourceNode) {
        this.sourceNode.onended = null;
        try { this.sourceNode.stop(); } catch (e) { /* 이미 멈춰 있어도 무시 */ }
        this.sourceNode = null;
      }
      this.playing = false;
      this.offset = 0;
    }

    get duration() { return this.buffer ? this.buffer.duration : 0; }

    get currentTime() {
      if (!this.buffer) return 0;
      const t = this.playing ? this.ctx.currentTime - this.startedAt : this.offset;
      return Math.min(t, this.buffer.duration);
    }

    // bands: colorModel 의 bands 목록. 반환값: 각 밴드의 0..1 정규화 레벨(Float32Array)
    getBandLevels(bands, out, harmonicSuppress = 0.7) {
      if (!this.analyser) return out;
      this.analyser.getFloatFrequencyData(this.freqData);
      this._adjusted = emphasizeFundamentals(this.freqData, this.ctx.sampleRate, this.analyser.fftSize, this.minDb, this.maxDb, harmonicSuppress, this._adjusted);
      return interpolateBandLevels(this._adjusted, bands, this.ctx.sampleRate, this.analyser.fftSize, this.minDb, this.maxDb, out);
    }

    // "채색 악보" 모드용: 지금 재생 중인 부분과 무관하게, 불러온 곡 전체를 OfflineAudioContext로
    // totalSlices개 시점에서 스냅샷을 찍어 bands별 레벨을 한 번에 계산한다(곡 하나 = 고정된
    // 색면 하나). suspend(t)/resume() 쌍으로 렌더링을 그 시점에서 멈추고 analyser로 스펙트럼을
    // 읽은 뒤 계속 진행하는, OfflineAudioContext의 표준적인 스냅샷 패턴을 쓴다.
    async analyzeBuffer(bands, { totalSlices, harmonicSuppress = 0.7 } = {}) {
      if (!this.buffer) return [];
      const buffer = this.buffer;
      const sampleRate = buffer.sampleRate;
      const offlineCtx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(
        buffer.numberOfChannels, buffer.length, sampleRate
      );
      const source = offlineCtx.createBufferSource();
      source.buffer = buffer;
      const analyser = offlineCtx.createAnalyser();
      analyser.fftSize = FFT_SIZE;
      analyser.minDecibels = MIN_DB;
      analyser.maxDecibels = MAX_DB;
      analyser.smoothingTimeConstant = 0;
      source.connect(analyser);
      analyser.connect(offlineCtx.destination);

      const hopSeconds = buffer.duration / totalSlices;
      const scratch = new Float32Array(analyser.frequencyBinCount);
      const levels = new Array(totalSlices);
      let adjustedScratch = null;

      const waits = [];
      for (let i = 0; i < totalSlices; i++) {
        const t = Math.min(i * hopSeconds, buffer.duration - 1e-4);
        const wait = offlineCtx.suspend(t).then(() => {
          analyser.getFloatFrequencyData(scratch);
          adjustedScratch = emphasizeFundamentals(scratch, sampleRate, FFT_SIZE, MIN_DB, MAX_DB, harmonicSuppress, adjustedScratch);
          const out = new Float32Array(bands.length);
          interpolateBandLevels(adjustedScratch, bands, sampleRate, FFT_SIZE, MIN_DB, MAX_DB, out);
          levels[i] = out;
          return offlineCtx.resume();
        });
        waits.push(wait);
      }

      source.start(0);
      await offlineCtx.startRendering();
      await Promise.all(waits);
      return levels;
    }
  }

  g.AudioEngine = AudioEngine;
})(window);
