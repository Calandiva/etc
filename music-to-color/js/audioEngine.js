/* Web Audio 재생 + FFT 분석. 밴드별 레벨은 "그 주파수에서 가장 가까운 두 FFT bin 사이 선형보간"으로
   구해서, 격자 칸 수(최대 1000x1000)가 FFT 해상도보다 훨씬 많아도 빈 칸 없이 매끈하게 채운다.

   실제 음원은 기음(기본주파수) 하나에 배음(정수배 倍音)이 줄줄이 따라붙어, 배음들도 꽤 크게
   잡히는 바람에 모든 대역이 한꺼번에 울렁거리는 문제가 있다. _emphasizeFundamentals() 가 매
   프레임 스펙트럼 피크를 찾아, 가장 강한 피크(기음)는 그대로 두고 그 정수배 주파수에 걸리는
   약한 피크(배음)는 어둡게 깎아서, 실제로 울리는 음 각각이 또렷한 하나의 밝은 신호로 보이게
   한다. */
(function (g) {
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
        this.setFftSize(8192);
        this.setDecibelRange(-90, -10);
        this.analyser.connect(this.ctx.destination);
      }
      return this.ctx;
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

    // 매 프레임 스펙트럼에서 피크를 찾아 기음/배음을 분류하고, 배음 피크 주변을 어둡게 깎은
    // 보정 스펙트럼을 돌려준다. strength 0 = 보정 없음(원본 그대로), 1 = 강하게 깎음.
    _emphasizeFundamentals(strength) {
      const src = this.freqData, n = src.length;
      if (!this._adjusted || this._adjusted.length !== n) this._adjusted = new Float32Array(n);
      const adjusted = this._adjusted;
      adjusted.set(src);
      if (strength <= 0) return adjusted;

      const binHz = this.ctx.sampleRate / this.analyser.fftSize;
      const span = this.maxDb - this.minDb;
      const threshold = this.minDb + 0.12 * span;
      const minSeparationHz = 20;

      const peaks = [];
      for (let i = 1; i < n - 1; i++) {
        const v = src[i];
        if (v >= threshold && v > src[i - 1] && v >= src[i + 1]) peaks.push({ i, freq: i * binHz, db: v });
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

    // bands: colorModel.buildLayout() 의 bands 목록. 반환값: 각 밴드의 0..1 정규화 레벨(Float32Array)
    getBandLevels(bands, out, harmonicSuppress = 0.7) {
      if (!this.analyser) return out;
      this.analyser.getFloatFrequencyData(this.freqData);
      const spectrum = this._emphasizeFundamentals(harmonicSuppress);
      const sampleRate = this.ctx.sampleRate;
      const fftSize = this.analyser.fftSize;
      const n = spectrum.length;
      const span = this.maxDb - this.minDb;
      for (let i = 0; i < bands.length; i++) {
        const binPos = (bands[i].freqHz * fftSize) / sampleRate;
        const lo = Math.max(0, Math.min(n - 1, Math.floor(binPos)));
        const hi = Math.max(0, Math.min(n - 1, lo + 1));
        const frac = binPos - lo;
        const db = spectrum[lo] + (spectrum[hi] - spectrum[lo]) * frac;
        out[i] = Math.max(0, Math.min(1, (db - this.minDb) / span));
      }
      return out;
    }
  }

  g.AudioEngine = AudioEngine;
})(window);
