/* 轻量 SVG 图表库（无外部依赖）。支持折线、区间带、柱状、仪表盘。 */
(function (global) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const PALETTE = ["#2F6BFF", "#00B3A4", "#FF8A3D", "#B44DFF", "#E5484D", "#F5A623", "#6B7A8F"];

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function svgRoot(container, w, h) {
    container.innerHTML = "";
    const svg = el("svg", {
      viewBox: `0 0 ${w} ${h}`,
      preserveAspectRatio: "none",
      width: "100%",
      height: h + "px",
      style: "display:block",
    });
    container.appendChild(svg);
    return svg;
  }

  function niceTicks(min, max, count) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norms = [1, 2, 2.5, 5, 10];
    let step = mag;
    for (const n of norms) { if (step0 <= n * mag) { step = n * mag; break; } }
    const start = Math.floor(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 0.5; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  function fmt(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return Number(v.toFixed(digits === undefined ? 2 : digits)).toString();
  }

  function textWidth(t) {
    let w = 0;
    for (const ch of String(t)) w += ch.charCodeAt(0) > 0x2e80 ? 12 : 6.6;
    return w;
  }

  function line(container, opts) {
    const W = 1000;
    const H = opts.height || 260;
    const pad = { l: 56, r: 16, t: 16, b: 30 };
    const svg = svgRoot(container, W, H);
    const series = (opts.series || []).filter(s => s && s.data && s.data.length);
    const labels = opts.labels || [];
    const n = Math.max(...series.map(s => s.data.length), 1);

    // 先排版图例（可换行），再据此预留顶部空间，避免与曲线/标注拥挤
    const showLegend = opts.legend !== false && series.length > 0;
    const legendRows = [];
    if (showLegend) {
      const maxW = W - pad.l - pad.r;
      const gap = 26, swatch = 14;
      let row = [], lx = 0;
      series.forEach((s, si) => {
        const name = s.name || ("系列 " + (si + 1));
        const w = swatch + textWidth(name) + gap;
        if (lx + w > maxW && row.length) { legendRows.push(row); row = []; lx = 0; }
        row.push({ name, x: lx, w, color: s.color || PALETTE[si % PALETTE.length], opacity: s.opacity || 1 });
        lx += w;
      });
      if (row.length) legendRows.push(row);
      pad.t = 14 + legendRows.length * 20;
    }

    let vals = [];
    series.forEach(s => s.data.forEach(v => { if (v !== null && isFinite(v)) vals.push(v); }));
    if (opts.band) {
      (opts.band.lower || []).forEach(v => { if (v != null && isFinite(v)) vals.push(v); });
      (opts.band.upper || []).forEach(v => { if (v != null && isFinite(v)) vals.push(v); });
    }
    if (opts.yMin !== undefined) vals.push(opts.yMin);
    if (opts.yMax !== undefined) vals.push(opts.yMax);
    let ymin = opts.yMin !== undefined ? opts.yMin : Math.min(...vals);
    let ymax = opts.yMax !== undefined ? opts.yMax : Math.max(...vals);
    if (!vals.length) { ymin = 0; ymax = 1; }
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    const padY = (ymax - ymin) * 0.08;
    ymin -= padY; ymax += padY;
    const x = i => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const y = v => pad.t + (1 - (v - ymin) / (ymax - ymin)) * (H - pad.t - pad.b);

    const ticks = niceTicks(ymin, ymax, 5);
    ticks.forEach(t => {
      const yy = y(t);
      if (yy < pad.t - 1 || yy > H - pad.b + 1) return;
      el("line", { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, stroke: "#EAEEF4", "stroke-width": 1 }, svg);
      const tx = el("text", { x: pad.l - 8, y: yy + 3, "text-anchor": "end", fill: "#93A1B5", "font-size": 11 }, svg);
      tx.textContent = fmt(t, 2);
    });

    if (opts.band && opts.band.lower && opts.band.upper) {
      const lo = opts.band.lower, up = opts.band.upper;
      let d = "";
      for (let i = 0; i < up.length; i++) if (up[i] != null) d += (d ? "L" : "M") + x(i) + " " + y(up[i]) + " ";
      for (let i = lo.length - 1; i >= 0; i--) if (lo[i] != null) d += "L" + x(i) + " " + y(lo[i]) + " ";
      if (d) el("path", { d: d + "Z", fill: opts.band.color || "rgba(47,107,255,0.12)", stroke: "none" }, svg);
    }

    const seriesPath = s => {
      let d = "", started = false;
      s.data.forEach((v, i) => {
        if (v == null || !isFinite(v)) { started = false; return; }
        d += (started ? "L" : "M") + x(i) + " " + y(v) + " ";
        started = true;
      });
      return d;
    };

    // 白色描边（halo）：多条曲线交叉/重叠时区分更清晰
    if (opts.halo) {
      series.forEach((s, si) => {
        const d = seriesPath(s);
        if (d) el("path", { d, fill: "none", stroke: "#FFFFFF", "stroke-width": (s.width || 2) + 3.5, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      });
    }

    series.forEach((s, si) => {
      const color = s.color || PALETTE[si % PALETTE.length];
      const d = seriesPath(s);
      if (d) el("path", {
        d, fill: "none", stroke: color, "stroke-width": s.width || 2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        "stroke-dasharray": s.dash || "none", opacity: s.opacity || 1,
      }, svg);
    });

    const idxs = n > 1 ? [0, Math.floor((n - 1) / 2), n - 1] : [0];
    idxs.forEach(i => {
      const label = labels[i];
      if (!label) return;
      const tx = el("text", { x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : (i === n - 1 ? "end" : "middle"), fill: "#93A1B5", "font-size": 11 }, svg);
      tx.textContent = label;
    });

    if (showLegend) {
      legendRows.forEach((row, ri) => {
        const ly = 14 + ri * 20;
        row.forEach(item => {
          const lx = pad.l + item.x;
          el("rect", { x: lx, y: ly - 8, width: 11, height: 11, rx: 3, fill: item.color, opacity: item.opacity }, svg);
          const tx = el("text", { x: lx + 16, y: ly + 1, fill: "#5B6B84", "font-size": 11.5 }, svg);
          tx.textContent = item.name;
        });
      });
    }
    return svg;
  }

  function wrapLabel(text, maxW) {
    const s = String(text);
    const out = [];
    let cur = "";
    for (const ch of s) {
      if (cur && textWidth(cur + ch) > maxW) { out.push(cur); cur = ch; }
      else cur += ch;
    }
    out.push(cur);
    return out.length ? out : [""];
  }

  function bar(container, opts) {
    const W = 1000;
    const items = opts.items || [];
    const angle = opts.rotateAngle || -45;
    const pad = { l: 56, r: 20, t: 16, b: 30 };
    const bw = (W - pad.l - pad.r) / Math.max(items.length, 1);

    // 标签排版：旋转标签按最长名预留底部空间；横向标签自动换行
    let wrapped = null, maxLines = 1;
    if (opts.rotate) {
      const maxLabelW = Math.max(...items.map(it => textWidth(it.label || "")), 0);
      pad.b = Math.min(170, Math.ceil(maxLabelW * Math.sin(Math.abs(angle) * Math.PI / 180)) + 24);
    } else if (opts.horizontal !== false) {
      wrapped = items.map(it => wrapLabel(it.label || "", bw - 10));
      maxLines = Math.max(...wrapped.map(w => w.length), 1);
      pad.b = 12 + maxLines * 15;
    }

    const H = opts.height || (opts.rotate ? 260 + pad.b : 200 + pad.b);
    const svg = svgRoot(container, W, H);
    const vals = items.map(it => it.value).filter(v => v != null && isFinite(v));
    let ymax = vals.length ? Math.max(...vals) : 1;
    let ymin = Math.min(0, vals.length ? Math.min(...vals) : 0);
    const ticks = niceTicks(ymin, ymax, 5);
    const y = v => pad.t + (1 - (v - ymin) / (ymax - ymin || 1)) * (H - pad.t - pad.b);
    ticks.forEach(t => {
      const yy = y(t);
      el("line", { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, stroke: "#EAEEF4" }, svg);
      const tx = el("text", { x: pad.l - 8, y: yy + 3, "text-anchor": "end", fill: "#93A1B5", "font-size": 11 }, svg);
      tx.textContent = fmt(t, 2);
    });
    items.forEach((it, i) => {
      const cx = pad.l + i * bw;
      const color = it.color || PALETTE[i % PALETTE.length];
      if (it.value != null && isFinite(it.value)) {
        const yy = y(it.value);
        const top = Math.min(yy, y(0));
        el("rect", { x: cx + bw * 0.15, y: top, width: bw * 0.7, height: Math.abs(y(0) - yy), rx: 3, fill: color, opacity: 0.9 }, svg);
        const vt = el("text", { x: cx + bw / 2, y: top - 5, "text-anchor": "middle", fill: "#5B6B84", "font-size": 11 }, svg);
        vt.textContent = fmt(it.value, opts.digits);
      }
      if (opts.rotate) {
        const ay = H - pad.b + 4;
        const lt = el("text", {
          x: cx + bw / 2, y: ay, "text-anchor": "end", fill: "#93A1B5", "font-size": 11,
          transform: `rotate(${angle} ${cx + bw / 2} ${ay})`,
        }, svg);
        lt.textContent = it.label;
      } else {
        const lines = (wrapped ? wrapped[i] : [it.label || ""]);
        const y0 = H - pad.b + 14;
        lines.forEach((ln, li) => {
          const lt = el("text", {
            x: cx + bw / 2, y: y0 + li * 15, "text-anchor": "middle", fill: "#93A1B5", "font-size": 11,
          }, svg);
          lt.textContent = ln;
        });
      }
    });
    return svg;
  }

  function hbar(container, opts) {
    const W = 1000;
    const items = opts.items || [];
    const rowH = opts.rowH || 26;
    const H = opts.height || Math.max(120, items.length * rowH + 24);
    const pad = { l: opts.labelWidth || 170, r: 64, t: 8, b: 12 };
    const svg = svgRoot(container, W, H);
    const maxV = Math.max(...items.map(i => (i.value != null && isFinite(i.value)) ? i.value : 0), 1);
    items.forEach((it, i) => {
      const cy = pad.t + i * rowH + rowH / 2;
      const tx = el("text", { x: pad.l - 12, y: cy + 4, "text-anchor": "end", fill: "#5B6B84", "font-size": 12 }, svg);
      tx.textContent = it.label;
      el("rect", { x: pad.l, y: cy - 8, width: W - pad.l - pad.r, height: 16, rx: 4, fill: "#F1F5FB" }, svg);
      const w = (it.value != null && isFinite(it.value)) ? Math.max(0, (it.value / maxV) * (W - pad.l - pad.r)) : 0;
      el("rect", { x: pad.l, y: cy - 8, width: w, height: 16, rx: 4, fill: it.color || PALETTE[i % PALETTE.length], opacity: 0.9 }, svg);
      const vt = el("text", { x: pad.l + w + 8, y: cy + 4, fill: "#5B6B84", "font-size": 11 }, svg);
      vt.textContent = fmt(it.value, opts.digits);
    });
    return svg;
  }

  function gauge(container, opts) {
    const W = 260, H = 224;
    const size = opts.size || 260;
    container.innerHTML = "";
    const svg = el("svg", {
      viewBox: `0 0 ${W} ${H}`, width: size, height: size * H / W,
      style: "display:block;margin:0 auto",
    });
    container.appendChild(svg);
    const cx = W / 2, cy = H - 78, r = 96;
    const min = opts.min || 0, max = opts.max || 100;
    const val = Math.max(min, Math.min(max, opts.value == null ? min : opts.value));
    const frac = (val - min) / (max - min || 1);
    const span = Math.PI * 200 / 180;      // 200° 扇形量程
    const startTh = Math.PI + (span - Math.PI) / 2;   // 略低于水平线
    const pt = (th, rad) => [cx + rad * Math.cos(th), cy - rad * Math.sin(th)];
    const endTh = startTh - frac * span;
    const color = opts.color || (frac >= 0.8 ? "#00B3A4" : frac >= 0.5 ? "#F5A623" : "#E5484D");
    const [sx, sy] = pt(startTh, r), [ex, ey] = pt(endTh, r);
    const [rx, ry] = pt(startTh - span, r);

    // 底弧（浅色轨道）
    el("path", { d: `M ${sx} ${sy} A ${r} ${r} 0 1 1 ${rx} ${ry}`, fill: "none", stroke: "#EEF2F7", "stroke-width": 3 }, svg);
    // 扇形进度：从起点到当前值的实心扇形
    if (frac > 0.001) {
      const large = frac * span > Math.PI ? 1 : 0;
      el("path", { d: `M ${cx} ${cy} L ${sx} ${sy} A ${r} ${r} 0 ${large} 1 ${ex} ${ey} Z`, fill: color, opacity: 0.92 }, svg);
    }
    // 外圈刻度：按数值等分（每 10% 一格），主刻度标注数值，位置严格对应量程
    const tickN = opts.tickIntervals || 10;
    for (let k = 0; k <= tickN; k++) {
      const f = k / tickN;
      const major = (k % (tickN / 2) === 0);   // 起点 / 中点 / 终点
      const th = startTh - f * span;
      const [x0, y0] = pt(th, major ? r + 8 : r + 10);
      const [x1, y1] = pt(th, major ? r + 18 : r + 15);
      el("line", { x1: x0, y1: y0, x2: x1, y2: y1, stroke: major ? "#8A97A8" : "#B9C4D6", "stroke-width": major ? 2 : 1.2, "stroke-linecap": "round" }, svg);
      if (major && opts.tickLabels !== false) {
        const [lx, ly] = pt(th, r + 24);
        const t = el("text", { x: Math.max(12, Math.min(W - 12, lx)), y: ly + 4, "text-anchor": "middle", fill: "#93A1B5", "font-size": 11 }, svg);
        t.textContent = Math.round(min + f * (max - min));
      }
    }
    // 指针与轴心
    const [nx, ny] = pt(endTh, r - 16);
    el("line", { x1: cx, y1: cy, x2: nx, y2: ny, stroke: "#1F2A3D", "stroke-width": 4, "stroke-linecap": "round" }, svg);
    el("circle", { cx, cy, r: 6.5, fill: "#1F2A3D" }, svg);
    el("circle", { cx, cy, r: 2.6, fill: "#FFFFFF" }, svg);
    // 标签 / 数值 / 占比
    if (opts.label) {
      const st = el("text", { x: cx, y: cy + 20, "text-anchor": "middle", fill: color, "font-size": 13, "font-weight": 700 }, svg);
      st.textContent = opts.label;
    }
    const vt = el("text", { x: cx, y: cy + 48, "text-anchor": "middle", fill: "#1F2A3D", "font-size": 30, "font-weight": 800 }, svg);
    vt.textContent = fmt(val, opts.digits === undefined ? 0 : opts.digits);
    if (opts.sub) {
      const sb = el("text", { x: cx, y: cy + 68, "text-anchor": "middle", fill: "#93A1B5", "font-size": 12 }, svg);
      sb.textContent = opts.sub;
    }
    return svg;
  }

  global.Charts = { line, bar, hbar, gauge, PALETTE, fmt };
})(window);
