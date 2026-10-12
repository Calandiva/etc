/* 시각화 렌더러 — 격자형(음계/주파수)은 작은 오프스크린 캔버스에 ImageData 를 채워 확대
   그리고, 방사형·사이클·관계형·직선형·채색 악보는 화면 캔버스에 직접 도형을 그린다. 전부
   render(levels, bands, minBrightness) 하나로 호출하고, 어떤 모양으로 그릴지는 setLayout()
   에서 받은 kind 가 정한다. */
(function (g) {
  const rgbCss = (c, brightness) =>
    `rgb(${Math.round(c[0] * brightness)},${Math.round(c[1] * brightness)},${Math.round(c[2] * brightness)})`;
  const brightnessOf = (level, minBrightness) =>
    minBrightness + (1 - minBrightness) * Math.sqrt(Math.max(0, Math.min(1, level)));

  class Visualizer {
    constructor(canvas, container) {
      this.canvas = canvas;
      this.container = container;
      this.ctx = canvas.getContext("2d");
      this.offscreen = document.createElement("canvas");
      this.offCtx = this.offscreen.getContext("2d", { willReadFrequently: false });
      this.kind = "grid";
      this.cols = 0;
      this.rows = 0;
      this.imageData = null;
      this.pixelated = true;
      this.ro = new ResizeObserver(() => this.layout());
      this.ro.observe(container);
      document.addEventListener("fullscreenchange", () => this.layout());
    }

    // layout: { kind: 'grid'|'radial'|'cycle'|'relational'|'linear'|'score', cols?, rows? }
    setLayout({ kind, cols, rows }) {
      this.kind = kind;
      if (kind === "grid") {
        this.cols = cols;
        this.rows = rows;
        this.offscreen.width = cols;
        this.offscreen.height = rows;
        this.imageData = this.offCtx.createImageData(cols, rows);
      } else if (kind === "score") {
        this.cols = cols; // 채색 악보는 벡터로 직접 그려서 오프스크린 버퍼가 필요 없다
        this.rows = rows;
      }
      this.layout();
    }

    setPixelated(v) { this.pixelated = v; }

    clear() { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); }

    layout() {
      const dpr = window.devicePixelRatio || 1;
      const w = this.container.clientWidth, h = this.container.clientHeight;
      if (w <= 0 || h <= 0) return;
      let cssW = w, cssH = h;
      if (this.kind === "grid" && this.cols && this.rows) {
        const aspect = this.cols / this.rows;
        cssH = w / aspect;
        if (cssH > h) { cssH = h; cssW = h * aspect; }
      } else if (this.kind !== "linear" && this.kind !== "score") {
        const side = Math.min(w, h);
        cssW = side; cssH = side;
      }
      // 'linear'·'score'는 컨테이너를 그대로 꽉 채운다(score는 각 줄에 충분한 두께를 주기 위해)
      this.canvas.style.width = cssW + "px";
      this.canvas.style.height = cssH + "px";
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }

    render(levels, bands, minBrightness) {
      if (!bands) return;
      if (this.kind !== "score" && !bands.length) return; // score는 점이 0개여도(무음 구간) 지워야 한다
      if (this.kind === "grid") this._renderGrid(levels, bands, minBrightness);
      else if (this.kind === "radial") this._renderRadial(levels, bands, minBrightness);
      else if (this.kind === "cycle") this._renderRing(levels, bands, minBrightness, false);
      else if (this.kind === "relational") this._renderRing(levels, bands, minBrightness, true);
      else if (this.kind === "linear") this._renderLinear(levels, bands, minBrightness);
      else if (this.kind === "score") this._renderScore(levels, bands, minBrightness);
    }

    // "채색 악보"처럼 고정 이미지를 그려놓은 위에, 지금 재생 위치에 해당하는 한 줄의 한 칸을
    // 가볍게 덧그린다 — render()가 매번 다시 그리는 큰 이미지는 그대로 두고 테두리만.
    markPlayhead(col, rowStart, rowSpan) {
      if ((this.kind !== "grid" && this.kind !== "score") || !this.cols || !this.rows) return;
      const sx = this.canvas.width / this.cols, sy = this.canvas.height / this.rows;
      this.ctx.strokeStyle = "rgba(255,255,255,0.9)";
      this.ctx.lineWidth = Math.max(1, sx * 0.18);
      this.ctx.strokeRect(col * sx + this.ctx.lineWidth / 2, rowStart * sy + this.ctx.lineWidth / 2, sx - this.ctx.lineWidth, rowSpan * sy - this.ctx.lineWidth);
    }

    // 격자형: cols x rows 픽셀짜리 오프스크린에 채운 뒤 화면 캔버스로 확대(최대 1,000,000칸도 가볍다)
    _renderGrid(levels, bands, minBrightness) {
      const data = this.imageData.data;
      for (let i = 0; i < bands.length; i++) {
        const b = bands[i];
        const brightness = brightnessOf(levels[i], minBrightness);
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

    // 방사형: 연속 스펙트럼 밴드를 원 한 바퀴에 펼쳐 놓고, 소리가 커지면 그 방향으로 반지름이 늘어난다
    _renderRadial(levels, bands, minBrightness) {
      const w = this.canvas.width, h = this.canvas.height;
      const cx = w / 2, cy = h / 2, maxR = Math.min(w, h) / 2;
      const innerR = maxR * 0.16, outerR = maxR * 0.92;
      this.ctx.clearRect(0, 0, w, h);
      this.ctx.lineWidth = Math.max(1, maxR * 0.01);
      this.ctx.lineJoin = "round";
      let prev = null;
      const n = bands.length;
      for (let i = 0; i <= n; i++) {
        const idx = i % n;
        const b = bands[idx];
        const brightness = brightnessOf(levels[idx], minBrightness);
        const angle = b.t * Math.PI * 2 - Math.PI / 2;
        const r = innerR + brightness * (outerR - innerR);
        const x = cx + r * Math.cos(angle), y = cy + r * Math.sin(angle);
        if (prev) {
          this.ctx.strokeStyle = rgbCss(b.color, brightness);
          this.ctx.beginPath();
          this.ctx.moveTo(prev.x, prev.y);
          this.ctx.lineTo(x, y);
          this.ctx.stroke();
        }
        this.ctx.fillStyle = rgbCss(b.color, brightness);
        this.ctx.beginPath();
        this.ctx.arc(x, y, Math.max(1.5, maxR * 0.012), 0, Math.PI * 2);
        this.ctx.fill();
        prev = { x, y };
      }
    }

    // 사이클·관계형 공용: 음이름 12개를 원 둘레에 고정 배치(순서는 app.js 가 전달하는 bands 순서가
    // 결정 — 5도권 순서면 "사이클", 반음 순서면 "관계형"). connectActive=true면 동시에 울리는
    // 음끼리 선으로 이어 화음 모양을 보여준다.
    _renderRing(levels, bands, minBrightness, connectActive) {
      const w = this.canvas.width, h = this.canvas.height;
      const cx = w / 2, cy = h / 2, maxR = Math.min(w, h) / 2;
      const ringR = maxR * 0.66;
      this.ctx.clearRect(0, 0, w, h);

      const n = bands.length;
      const points = bands.map((_, i) => {
        const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
        return { x: cx + ringR * Math.cos(angle), y: cy + ringR * Math.sin(angle), angle };
      });

      if (connectActive) {
        this.ctx.lineWidth = Math.max(1, maxR * 0.006);
        for (let i = 0; i < n; i++) {
          for (let j = i + 1; j < n; j++) {
            const li = levels[i], lj = levels[j];
            if (li >= 0.42 && lj >= 0.42) {
              const a = Math.min(li, lj);
              this.ctx.strokeStyle = `rgba(230,230,239,${(0.15 + 0.55 * a).toFixed(3)})`;
              this.ctx.beginPath();
              this.ctx.moveTo(points[i].x, points[i].y);
              this.ctx.lineTo(points[j].x, points[j].y);
              this.ctx.stroke();
            }
          }
        }
      }

      const baseDot = maxR * 0.045, extraDot = maxR * 0.055;
      const fontSize = Math.max(10, maxR * 0.07);
      this.ctx.font = `${fontSize}px -apple-system, "Pretendard", sans-serif`;
      this.ctx.textAlign = "center";
      this.ctx.textBaseline = "middle";
      for (let i = 0; i < n; i++) {
        const b = bands[i];
        const brightness = brightnessOf(levels[i], minBrightness);
        const p = points[i];
        const dotR = baseDot + brightness * extraDot;
        this.ctx.fillStyle = rgbCss(b.color, brightness);
        this.ctx.beginPath();
        this.ctx.arc(p.x, p.y, dotR, 0, Math.PI * 2);
        this.ctx.fill();

        const labelR = ringR + baseDot + extraDot + fontSize * 0.9;
        const lx = cx + labelR * Math.cos(p.angle), ly = cy + labelR * Math.sin(p.angle);
        this.ctx.fillStyle = "rgba(230,230,239,0.85)";
        this.ctx.fillText(b.label.replace(/[0-9]/g, ""), lx, ly);
      }
    }

    // 채색 악보: 줄(this.rows) x 칸(this.cols)의 접힌 시간축 위에, 각 시간 칸에서 실제로 울리던
    // 기음들을 섞은 색 점 하나씩만 찍는다 — 배음·침묵 구간은 그리지 않아서 전체적으로 땡땡이
    // 무늬가 된다. 점의 세로 위치(band.t, 0~1)는 그 칸의 혼합 음높이가 곡 전체 기음 범위에서
    // 어디쯌인지, 점의 크기·밝기는 levels[i](세기)가 정한다.
    _renderScore(levels, bands, minBrightness) {
      const w = this.canvas.width, h = this.canvas.height;
      this.ctx.clearRect(0, 0, w, h);
      if (!bands.length || !this.cols || !this.rows) return;
      const colW = w / this.cols, rowH = h / this.rows;
      const maxRadius = Math.min(colW, rowH) * 0.42, minRadius = maxRadius * 0.22;
      for (let i = 0; i < bands.length; i++) {
        const b = bands[i];
        const confidence = Math.max(0, Math.min(1, levels[i]));
        const brightness = minBrightness + (1 - minBrightness) * Math.sqrt(confidence);
        const radius = minRadius + confidence * (maxRadius - minRadius);
        const cx = (b.col + 0.5) * colW;
        const cy = (b.row + (1 - b.t)) * rowH; // t=1(고음) 쪽이 줄 위쪽
        this.ctx.fillStyle = rgbCss(b.color, brightness);
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        this.ctx.fill();
      }
    }

    // 직선형: 왼쪽부터 오른쪽으로 저음->고음, 클래식 이퀄라이저 막대 그래프
    _renderLinear(levels, bands, minBrightness) {
      const w = this.canvas.width, h = this.canvas.height;
      this.ctx.clearRect(0, 0, w, h);
      const n = bands.length;
      const slot = w / n, gap = slot * 0.18, barW = slot - gap;
      for (let i = 0; i < n; i++) {
        const brightness = brightnessOf(levels[i], minBrightness);
        const barH = Math.max(2, brightness * h);
        this.ctx.fillStyle = rgbCss(bands[i].color, brightness);
        this.ctx.fillRect(i * slot + gap / 2, h - barH, barW, barH);
      }
    }
  }

  g.Visualizer = Visualizer;
})(window);
