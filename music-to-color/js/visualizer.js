/* cols x rows 격자 렌더러(정사각형 스윕형, 가로세로 다른 음계형 둘 다 지원). 작은 오프스크린
   캔버스(cols x rows, 칸당 1픽셀)에 ImageData 를 채운 뒤, 화면에 보이는 큰 캔버스로 확대
   그려서 1,000,000칸까지도 매 프레임 가볍게 갱신한다. */
(function (g) {
  class Visualizer {
    constructor(canvas, container) {
      this.canvas = canvas;
      this.container = container;
      this.ctx = canvas.getContext("2d");
      this.offscreen = document.createElement("canvas");
      this.offCtx = this.offscreen.getContext("2d", { willReadFrequently: false });
      this.cols = 0;
      this.rows = 0;
      this.imageData = null;
      this.pixelated = true;
      this.ro = new ResizeObserver(() => this.layout());
      this.ro.observe(container);
      document.addEventListener("fullscreenchange", () => this.layout());
    }

    setGridDims(cols, rows) {
      this.cols = cols;
      this.rows = rows;
      this.offscreen.width = cols;
      this.offscreen.height = rows;
      this.imageData = this.offCtx.createImageData(cols, rows);
      this.layout();
    }

    setPixelated(v) { this.pixelated = v; }

    layout() {
      const dpr = window.devicePixelRatio || 1;
      const w = this.container.clientWidth;
      const h = this.container.clientHeight;
      if (w <= 0 || h <= 0 || !this.cols || !this.rows) return;
      const aspect = this.cols / this.rows;
      let cssW = w, cssH = w / aspect;
      if (cssH > h) { cssH = h; cssW = h * aspect; }
      this.canvas.style.width = cssW + "px";
      this.canvas.style.height = cssH + "px";
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }

    // levels: 밴드별 0..1, bands: colorModel.buildLayout() 의 bands(각자 고정 row/col/color 보유)
    render(levels, bands, minBrightness) {
      if (!this.cols || !this.rows || !bands || !bands.length) return;
      const data = this.imageData.data;
      for (let i = 0; i < bands.length; i++) {
        const b = bands[i];
        const level = levels[i];
        const brightness = minBrightness + (1 - minBrightness) * Math.sqrt(Math.max(0, Math.min(1, level)));
        const c = b.color;
        const p = (b.row * this.cols + b.col) * 4;
        data[p] = c[0] * brightness;
        data[p + 1] = c[1] * brightness;
        data[p + 2] = c[2] * brightness;
        data[p + 3] = 255;
      }
      this.offCtx.putImageData(this.imageData, 0, 0);
      this.ctx.imageSmoothingEnabled = !this.pixelated;
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.ctx.drawImage(this.offscreen, 0, 0, this.cols, this.rows, 0, 0, this.canvas.width, this.canvas.height);
    }
  }

  g.Visualizer = Visualizer;
})(window);
