/* Web Audio 재생 + FFT 분석. 밴드별 레벨은 "그 주파수에서 가장 가까운 두 FFT bin 사이 선형보간"으로
   구해서, 격자 칸 수(최대 1000x1000)가 FFT 해상도보다 훨씬 많아도 빈 칸 없이 매끈하게 채운다. */
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

    // bands: colorModel.buildBands() 결과. 반환값: 각 밴드의 0..1 정규화 레벨(Float32Array)
    getBandLevels(bands, out) {
      if (!this.analyser) return out;
      this.analyser.getFloatFrequencyData(this.freqData);
      const sampleRate = this.ctx.sampleRate;
      const fftSize = this.analyser.fftSize;
      const n = this.freqData.length;
      const span = this.maxDb - this.minDb;
      for (let i = 0; i < bands.length; i++) {
        const binPos = (bands[i].freqHz * fftSize) / sampleRate;
        const lo = Math.max(0, Math.min(n - 1, Math.floor(binPos)));
        const hi = Math.max(0, Math.min(n - 1, lo + 1));
        const frac = binPos - lo;
        const db = this.freqData[lo] + (this.freqData[hi] - this.freqData[lo]) * frac;
        out[i] = Math.max(0, Math.min(1, (db - this.minDb) / span));
      }
      return out;
    }
  }

  g.AudioEngine = AudioEngine;
})(window);
