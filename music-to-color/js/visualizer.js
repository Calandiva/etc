/* N x N 격자 렌더러. 작은 오프스크린 캔버스(N x N, 칸당 1픽셀)에 ImageData 를 채운 뒤,
   화면에 보이는 큰 캔버스로 확대 그려서 1,000,000칸까지도 매 프레임 가볍게 갱신한다. */
(function (g) {
  class Visualizer {
    constructor(canvas, container) {
      this.canvas = canvas;
      this.container = container;
      this.ctx = canvas.getContext("2d");
      this.offscreen = document.createElement("canvas");
      this.offCtx = this.offscreen.getContext("2d", { willReadFrequently: false });
      this.gridSize = 0;
      this.imageData = null;
      this.pixelated = true;
      this.ro = new ResizeObserver(() => this.layout());
      this.ro.observe(container);
      document.addEventListener("fullscreenchange", () => this.layout());
    }

    setGridSize(n) {
      this.gridSize = n;
      this.offscreen.width = n;
      this.offscreen.height = n;
      this.imageData = this.offCtx.createImageData(n, n);
      this.layout();
    }

    setPixelated(v) { this.pixelated = v; }

    layout() {
      const dpr = window.devicePixelRatio || 1;
      const w = this.container.clientWidth;
      const h = this.container.clientHeight;
      if (w <= 0 || h <= 0) return;
      const side = Math.min(w, h);
      this.canvas.style.width = side + "px";
      this.canvas.style.height = side + "px";
      this.canvas.width = Math.round(side * dpr);
      this.canvas.height = Math.round(side * dpr);
    }

    // levels: 밴드별 0..1, bands: colorModel.buildBands() 결과(고정 색)
    render(levels, bands, minBrightness) {
      const n = this.gridSize;
      if (!n || !bands || !bands.length) return;
      const data = this.imageData.data;
      for (let i = 0; i < bands.length; i++) {
        const level = levels[i];
        const brightness = minBrightness + (1 - minBrightness) * Math.sqrt(Math.max(0, Math.min(1, level)));
        const c = bands[i].color;
        const p = i * 4;
        data[p] = c[0] * brightness;
        data[p + 1] = c[1] * brightness;
        data[p + 2] = c[2] * brightness;
        data[p + 3] = 255;
      }
      this.offCtx.putImageData(this.imageData, 0, 0);
      this.ctx.imageSmoothingEnabled = !this.pixelated;
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.ctx.drawImage(this.offscreen, 0, 0, n, n, 0, 0, this.canvas.width, this.canvas.height);
    }
  }

  g.Visualizer = Visualizer;
})(window);
