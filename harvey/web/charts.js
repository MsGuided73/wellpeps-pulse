'use strict';

// WellPeps Pulse — tiny hand-rolled SVG charts for the Analytics tab.
// No library, no eval, no inline style attributes: SVG is built with
// createElementNS + setAttribute (presentation attributes, not style), every
// label goes in through textContent, and positions are set through the CSSOM
// (el.style.left = ...), which the 'self' CSP allows.
//
// Mark specs (dataviz method): bars <= 24px with a 4px rounded data end and a
// square baseline, a 2px surface gap between stacked segments, 2px lines,
// >= 8px end dots with a 2px surface ring, hairline solid gridlines. Every
// chart host is keyboard-focusable: arrow keys move a cursor that shows the
// same tooltip as hover and announces it to screen readers.

const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const SURFACE = '#FFFFFF';
  const GRID = '#E6ECF4';
  const AXIS = '#D3DEEC';
  const INK_MUTED = '#5F6E80';
  const BAR_MAX = 24;
  const GAP = 2;
  const RADIUS = 4;
  const PAD = {top: 14, right: 14, bottom: 30, left: 44};
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // ── DOM helpers ──

  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v !== null && v !== undefined) node.setAttribute(k, String(v));
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function htmlEl(tag, cls, text, parent) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (parent) parent.appendChild(node);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // ── Numbers & labels ──

  function fmtInt(n) {
    return Number(n || 0).toLocaleString('en-US');
  }

  function fmtPct(v, digits) {
    return v === null || v === undefined ? '—' : (v * 100).toFixed(digits === undefined ? 1 : digits) + '%';
  }

  function fmtSigned(v, digits) {
    if (v === null || v === undefined) return '—';
    const sign = v > 0 ? '+' : v < 0 ? '−' : '±';
    return sign + Math.abs(v).toFixed(digits === undefined ? 2 : digits);
  }

  function bucketLabel(key, bucket, long) {
    const [y, m, d] = String(key).split('-').map(Number);
    const text = MONTHS[(m || 1) - 1] + ' ' + d;
    if (!long) return text;
    return (bucket === 'week' ? 'Week of ' : '') + text + ', ' + y;
  }

  function niceStep(max, count) {
    const raw = Math.max(max, 1) / Math.max(count, 1);
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / pow;
    return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * pow;
  }

  function ticks(max, count) {
    const step = Math.max(1, niceStep(max, count));
    const out = [];
    for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  }

  // ── Tooltip (one per page) & live region ──

  let tip = null;
  let live = null;

  function tipEl() {
    if (!tip) {
      tip = htmlEl('div', 'chart-tip hidden', null, document.body);
      tip.setAttribute('role', 'presentation');
      live = htmlEl('div', 'visually-hidden', null, document.body);
      live.setAttribute('aria-live', 'polite');
    }
    return tip;
  }

  // rows: [{value, label, color, key: 'line'|'rect'|'none'}]
  function showTip(host, x, y, head, rows, announce) {
    const t = tipEl();
    clear(t);
    htmlEl('div', 'chart-tip-head', head, t);
    for (const r of rows) {
      const row = htmlEl('div', 'chart-tip-row', null, t);
      const key = htmlEl('span', 'chart-tip-key ' + (r.key || 'line'), null, row);
      if (r.color) key.style.backgroundColor = r.color;
      htmlEl('b', null, r.value, row);
      htmlEl('span', 'chart-tip-label', r.label, row);
    }
    t.classList.remove('hidden');
    const box = host.getBoundingClientRect();
    const tw = t.offsetWidth, th = t.offsetHeight;
    let left = box.left + window.scrollX + x + 14;
    if (left + tw > window.scrollX + document.documentElement.clientWidth - 8) left = box.left + window.scrollX + x - tw - 14;
    left = Math.max(window.scrollX + 8, left);
    const top = Math.max(window.scrollY + 8, box.top + window.scrollY + y - th - 10);
    t.style.left = left + 'px';
    t.style.top = top + 'px';
    if (announce && live) live.textContent = head + ': ' + rows.map(r => r.label + ' ' + r.value).join(', ');
  }

  function hideTip() {
    if (tip) tip.classList.add('hidden');
  }

  // Keyboard + pointer cursor over n positions. render(i) draws the highlight
  // and shows the tooltip; returns nothing. host must be focusable.
  // Listeners from a previous render of the same host are removed first.
  function reset(host) {
    if (host._chartOff) host._chartOff();
    host._chartOff = null;
  }

  function cursor(host, n, positionOf, onMove, onActivate) {
    reset(host);
    const handlers = [];
    const on = (type, fn) => { host.addEventListener(type, fn); handlers.push([type, fn]); };
    host._chartOff = () => handlers.forEach(([type, fn]) => host.removeEventListener(type, fn));
    let current = -1;
    const move = (i, announce) => {
      if (!n) return;
      current = Math.max(0, Math.min(n - 1, i));
      onMove(current, announce);
    };
    on('keydown', ev => {
      const keys = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1};
      if (ev.key in keys) { ev.preventDefault(); move(current < 0 ? 0 : current + keys[ev.key], true); }
      else if (ev.key === 'Home') { ev.preventDefault(); move(0, true); }
      else if (ev.key === 'End') { ev.preventDefault(); move(n - 1, true); }
      else if ((ev.key === 'Enter' || ev.key === ' ') && onActivate && current >= 0) { ev.preventDefault(); onActivate(current); }
      else if (ev.key === 'Escape') { hideTip(); onMove(-1); current = -1; }
    });
    on('focus', () => {
      if (!host.matches(':focus-visible')) return;  // pointer focus: let the pointer drive
      move(current < 0 ? 0 : current, true);
    });
    on('blur', () => { hideTip(); onMove(-1); });
    on('pointermove', ev => {
      const box = host.getBoundingClientRect();
      const i = positionOf(ev.clientX - box.left, ev.clientY - box.top);
      if (i === null || i < 0) { hideTip(); onMove(-1); current = -1; return; }
      if (i !== current) move(i, false);
    });
    on('pointerleave', () => { if (document.activeElement !== host) { hideTip(); onMove(-1); current = -1; } });
    if (onActivate) {
      on('click', ev => {
        const box = host.getBoundingClientRect();
        const i = positionOf(ev.clientX - box.left, ev.clientY - box.top);
        if (i !== null && i >= 0) onActivate(i);
      });
    }
  }

  function frame(host, height) {
    clear(host);
    const width = Math.max(260, Math.floor(host.clientWidth || 600));
    const svg = svgEl('svg', {width, height, viewBox: '0 0 ' + width + ' ' + height, 'aria-hidden': 'true',
      focusable: 'false', class: 'chart-svg'}, host);
    return {svg, width, height};
  }

  function yAxis(svg, width, height, scaleTicks, y, format) {
    for (const v of scaleTicks) {
      const py = y(v);
      svgEl('line', {x1: PAD.left, x2: width - PAD.right, y1: py, y2: py, stroke: v === 0 ? AXIS : GRID,
        'stroke-width': 1, 'shape-rendering': 'crispEdges'}, svg);
      const label = svgEl('text', {x: PAD.left - 8, y: py + 4, 'text-anchor': 'end', class: 'chart-tick'}, svg);
      label.textContent = format(v);
    }
  }

  function xLabels(svg, labels, xCenter, height, bucket) {
    const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor((xCenter(labels.length - 1) - xCenter(0)) / 64 + 1))));
    labels.forEach((key, i) => {
      if (i % every !== 0 && i !== labels.length - 1) return;
      if (i !== labels.length - 1 && labels.length - 1 - i < every && i !== 0) return;
      const text = svgEl('text', {x: xCenter(i), y: height - 8, 'text-anchor': 'middle', class: 'chart-tick'}, svg);
      text.textContent = bucketLabel(key, bucket);
    });
  }

  // Rounded-top rect path: square at the baseline, 4px radius at the data end.
  function barPath(x, y, w, h, rounded) {
    if (h <= 0) return '';
    const r = rounded ? Math.min(RADIUS, w / 2, h) : 0;
    return 'M' + x + ',' + (y + h) + 'V' + (y + r) + (r ? 'Q' + x + ',' + y + ' ' + (x + r) + ',' + y : '') +
      'H' + (x + w - r) + (r ? 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) : '') + 'V' + (y + h) + 'Z';
  }

  // ── Stacked columns (counts, or 100% shares) ──
  // spec: {labels, bucket, series:[{label, color, values}], percent, height, format}
  function columns(host, spec) {
    const {svg, width, height} = frame(host, spec.height || 240);
    const n = spec.labels.length;
    const totals = spec.labels.map((_, i) => spec.series.reduce((a, s) => a + (s.values[i] || 0), 0));
    const max = spec.percent ? 1 : Math.max(1, ...totals);
    const scale = spec.percent ? [0, 0.25, 0.5, 0.75, 1] : ticks(max, 4);
    const top = scale[scale.length - 1];
    const plotH = height - PAD.top - PAD.bottom;
    const y = v => PAD.top + plotH - (v / top) * plotH;
    const band = (width - PAD.left - PAD.right) / Math.max(n, 1);
    const barW = Math.max(3, Math.min(BAR_MAX, band * 0.62));
    const xCenter = i => PAD.left + band * (i + 0.5);
    yAxis(svg, width, height, scale, y, spec.percent ? (v => Math.round(v * 100) + '%') : fmtInt);
    const hl = svgEl('rect', {x: 0, y: PAD.top, width: band, height: plotH, fill: '#EAF5FF', opacity: 0}, svg);
    svg.insertBefore(hl, svg.firstChild);
    spec.labels.forEach((_, i) => {
      let acc = 0;
      const visible = spec.series.map((s, j) => [j, s.values[i] || 0]).filter(([, v]) => v > 0);
      visible.forEach(([j, v], k) => {
        const y0 = y(acc), y1 = y(acc + v);
        const last = k === visible.length - 1;
        const h = Math.max(0, y0 - y1 - (last ? 0 : GAP));
        if (h > 0) svgEl('path', {d: barPath(xCenter(i) - barW / 2, y1 + (last ? 0 : GAP), barW, h, last),
          fill: spec.series[j].color}, svg);
        acc += v;
      });
    });
    xLabels(svg, spec.labels, xCenter, height, spec.bucket);
    const format = spec.format || (spec.percent ? (v => fmtPct(v)) : fmtInt);
    cursor(host, n, (px, py) => (px < PAD.left || px > width - PAD.right || py > height - PAD.bottom + 6) ? null
      : Math.min(n - 1, Math.floor((px - PAD.left) / band)), (i, announce) => {
      if (i < 0) { hl.setAttribute('opacity', 0); return; }
      hl.setAttribute('x', xCenter(i) - band / 2);
      hl.setAttribute('width', band);
      hl.setAttribute('opacity', 1);
      const rows = spec.series.map((s, j) => ({value: format(s.values[i] || 0, s, i), label: s.label, color: s.color, key: 'rect'}))
        .reverse();
      const head = bucketLabel(spec.labels[i], spec.bucket, true) + (spec.percent ? '' : ' · ' + fmtInt(totals[i]) + ' total');
      showTip(host, xCenter(i), y(spec.percent ? 1 : totals[i]), head, rows, announce);
    });
  }

  // ── Lines with a zero baseline (sentiment) ──
  // spec: {labels, bucket, series:[{label, color, values (null = gap), n}], domain:[lo, hi], height}
  function lines(host, spec) {
    const {svg, width, height} = frame(host, spec.height || 240);
    const n = spec.labels.length;
    const [lo, hi] = spec.domain || [-1, 1];
    const plotH = height - PAD.top - PAD.bottom;
    const y = v => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;
    const step = n > 1 ? (width - PAD.left - PAD.right - 16) / (n - 1) : 0;
    const x = i => PAD.left + 8 + (n > 1 ? step * i : (width - PAD.left - PAD.right) / 2);
    const scale = [];
    for (let v = lo; v <= hi + 1e-9; v += (hi - lo) / 4) scale.push(Math.round(v * 100) / 100);
    yAxis(svg, width, height, scale, y, v => fmtSigned(v, 1).replace('±', ''));
    svgEl('line', {x1: PAD.left, x2: width - PAD.right, y1: y(0), y2: y(0), stroke: '#8FA0B5', 'stroke-width': 1,
      'shape-rendering': 'crispEdges'}, svg);
    const cross = svgEl('line', {x1: 0, x2: 0, y1: PAD.top, y2: PAD.top + plotH, stroke: '#8FA0B5', 'stroke-width': 1, opacity: 0}, svg);
    for (const s of spec.series) {
      let d = '';
      let pen = false;
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) { pen = false; return; }
        d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
        pen = true;
      });
      if (d) svgEl('path', {d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round',
        'stroke-linecap': 'round'}, svg);
      // Dots where a point stands alone (no neighbours) and at the last point.
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) return;
        const alone = (i === 0 || s.values[i - 1] === null) && (i === n - 1 || s.values[i + 1] === null);
        const lastPoint = s.values.slice(i + 1).every(u => u === null || u === undefined);
        if (alone || lastPoint) svgEl('circle', {cx: x(i), cy: y(v), r: 4, fill: s.color, stroke: SURFACE, 'stroke-width': 2}, svg);
      });
    }
    xLabels(svg, spec.labels, x, height, spec.bucket);
    const dots = spec.series.map(s => svgEl('circle', {r: 5, fill: s.color, stroke: SURFACE, 'stroke-width': 2, opacity: 0}, svg));
    cursor(host, n, px => (px < PAD.left - 4 || px > width - PAD.right + 4) ? null
      : Math.max(0, Math.min(n - 1, Math.round(step ? (px - PAD.left - 8) / step : 0))), (i, announce) => {
      if (i < 0) { cross.setAttribute('opacity', 0); dots.forEach(d => d.setAttribute('opacity', 0)); return; }
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('opacity', 1);
      spec.series.forEach((s, j) => {
        const v = s.values[i];
        if (v === null || v === undefined) { dots[j].setAttribute('opacity', 0); return; }
        dots[j].setAttribute('cx', x(i)); dots[j].setAttribute('cy', y(v)); dots[j].setAttribute('opacity', 1);
      });
      const rows = spec.series.map(s => ({
        value: s.values[i] === null || s.values[i] === undefined ? 'n/a' : fmtSigned(s.values[i], 2),
        label: s.label + ' · ' + ((s.n && s.n[i]) || 0) + ' mention' + (((s.n && s.n[i]) || 0) === 1 ? '' : 's') +
          (s.values[i] === null || s.values[i] === undefined ? ' (too few to show)' : ''),
        color: s.color, key: 'line'}));
      showTip(host, x(i), PAD.top + 10, bucketLabel(spec.labels[i], spec.bucket, true), rows, announce);
    });
  }

  // ── Horizontal bars (emerging terms) ──
  // spec: {rows:[{label, value, badge, detail}], color, onSelect(i), format}
  function hbars(host, spec) {
    const rowH = 28;
    const labelW = Math.min(200, Math.max(110, Math.floor((host.clientWidth || 600) * 0.34)));
    const {svg, width, height} = frame(host, spec.rows.length * rowH + 8);
    const max = Math.max(1, ...spec.rows.map(r => r.value));
    const valueW = 44;
    const plotW = Math.max(40, width - labelW - valueW - 12);
    const hl = svgEl('rect', {x: 0, y: 0, width, height: rowH, fill: '#EAF5FF', opacity: 0, rx: 6}, svg);
    spec.rows.forEach((r, i) => {
      const cy = 4 + i * rowH + rowH / 2;
      const label = svgEl('text', {x: labelW - 10, y: cy + 4, 'text-anchor': 'end', class: 'chart-label'}, svg);
      label.textContent = r.label.length > 28 ? r.label.slice(0, 27) + '…' : r.label;
      const w = Math.max(2, (r.value / max) * plotW);
      const h = Math.min(BAR_MAX, rowH - 10);
      const x0 = labelW, y0 = cy - h / 2;
      const r4 = Math.min(RADIUS, h / 2, w);
      svgEl('path', {d: 'M' + x0 + ',' + y0 + 'H' + (x0 + w - r4) + 'Q' + (x0 + w) + ',' + y0 + ' ' + (x0 + w) + ',' + (y0 + r4) +
        'V' + (y0 + h - r4) + 'Q' + (x0 + w) + ',' + (y0 + h) + ' ' + (x0 + w - r4) + ',' + (y0 + h) + 'H' + x0 + 'Z',
        fill: spec.color}, svg);
      const val = svgEl('text', {x: x0 + w + 6, y: cy + 4, class: 'chart-value'}, svg);
      val.textContent = (spec.format || fmtInt)(r.value) + (r.badge ? '  ' + r.badge : '');
    });
    cursor(host, spec.rows.length, (px, py) => Math.min(spec.rows.length - 1, Math.floor((py - 4) / rowH)), (i, announce) => {
      if (i < 0) { hl.setAttribute('opacity', 0); return; }
      hl.setAttribute('y', 4 + i * rowH); hl.setAttribute('opacity', 1);
      const r = spec.rows[i];
      showTip(host, labelW + 20, 4 + i * rowH, r.label, (r.detail || []).map(d => ({value: d[1], label: d[0], key: 'none'})), announce);
    }, spec.onSelect);
  }

  // ── Heatmap (sequential blue ramp) ──
  // spec: {rows:[label], cols:[label], cells:[[{count, suppressed, terms}]], ramp:[hex light->dark], darkFrom}
  function heatmap(host, spec) {
    const colW = 84;
    const labelW = Math.min(150, Math.max(96, Math.floor((host.clientWidth || 600) * 0.2)));
    const cellH = 34;
    const headH = 44;
    const width = labelW + spec.cols.length * colW + 4;
    clear(host);
    const scroller = htmlEl('div', 'chart-scroll', null, host);
    const svg = svgEl('svg', {width, height: headH + spec.rows.length * cellH + 4, viewBox: '0 0 ' + width + ' ' +
      (headH + spec.rows.length * cellH + 4), 'aria-hidden': 'true', focusable: 'false', class: 'chart-svg'}, scroller);
    const max = Math.max(1, ...spec.cells.flat().map(c => c.count || 0));
    spec.cols.forEach((c, j) => {
      const words = String(c).split(' ');
      const half = Math.ceil(words.length / 2);
      [words.slice(0, half).join(' '), words.slice(half).join(' ')].forEach((line, k) => {
        if (!line) return;
        const t = svgEl('text', {x: labelW + j * colW + colW / 2, y: 16 + k * 14, 'text-anchor': 'middle', class: 'chart-tick'}, svg);
        t.textContent = line;
      });
    });
    const hl = svgEl('rect', {width: colW - 2, height: cellH - 2, fill: 'none', stroke: '#082B59', 'stroke-width': 2, rx: 5, opacity: 0}, svg);
    spec.rows.forEach((r, i) => {
      const t = svgEl('text', {x: labelW - 10, y: headH + i * cellH + cellH / 2 + 4, 'text-anchor': 'end', class: 'chart-label'}, svg);
      t.textContent = r.length > 20 ? r.slice(0, 19) + '…' : r;
      spec.cols.forEach((_, j) => {
        const cell = spec.cells[i][j];
        const x0 = labelW + j * colW + 1, y0 = headH + i * cellH + 1;
        const step = cell.count ? Math.min(spec.ramp.length - 1, Math.floor((cell.count / max) * (spec.ramp.length - 1))) : -1;
        svgEl('rect', {x: x0, y: y0, width: colW - GAP, height: cellH - GAP, rx: 4,
          fill: step >= 0 ? spec.ramp[step] : '#F1F4F8'}, svg);
        const label = svgEl('text', {x: x0 + (colW - GAP) / 2, y: y0 + cellH / 2 + 3, 'text-anchor': 'middle',
          class: 'chart-cell' + (step >= spec.darkFrom ? ' on-dark' : '')}, svg);
        label.textContent = cell.count ? fmtInt(cell.count) : cell.suppressed ? '<2' : '';
      });
    });
    host.classList.add('chart-heat');
    const ncol = spec.cols.length;
    cursor(host, spec.rows.length * ncol, (px, py) => {
      const scrollX = scroller.scrollLeft;
      const j = Math.floor((px + scrollX - labelW) / colW), i = Math.floor((py - headH) / cellH);
      return (i < 0 || j < 0 || i >= spec.rows.length || j >= ncol) ? null : i * ncol + j;
    }, (k, announce) => {
      if (k < 0) { hl.setAttribute('opacity', 0); return; }
      const i = Math.floor(k / ncol), j = k % ncol;
      const cell = spec.cells[i][j];
      hl.setAttribute('x', labelW + j * colW + 1); hl.setAttribute('y', headH + i * cellH + 1); hl.setAttribute('opacity', 1);
      const rows = [{value: cell.count ? fmtInt(cell.count) : cell.suppressed ? 'fewer than 2' : '0', label: 'complaint mentions', key: 'none'}]
        .concat((cell.terms || []).map(t => ({value: fmtInt(t.count), label: '“' + t.term + '”', key: 'none'})));
      showTip(host, labelW + j * colW + colW / 2 - scroller.scrollLeft, headH + i * cellH, spec.rows[i] + ' × ' + spec.cols[j], rows, announce);
    });
  }

  // ── Sparkline (small multiples) ──
  function sparkline(host, values, color) {
    clear(host);
    const width = Math.max(120, Math.floor(host.clientWidth || 160));
    const height = 40;
    const svg = svgEl('svg', {width, height, viewBox: '0 0 ' + width + ' ' + height, 'aria-hidden': 'true', focusable: 'false'}, host);
    const max = Math.max(1, ...values);
    const n = values.length;
    const x = i => 4 + (n > 1 ? (i / (n - 1)) * (width - 8) : (width - 8) / 2);
    const y = v => height - 5 - (v / max) * (height - 12);
    svgEl('line', {x1: 4, x2: width - 4, y1: height - 5, y2: height - 5, stroke: AXIS, 'stroke-width': 1}, svg);
    const pts = values.map((v, i) => x(i).toFixed(1) + ',' + y(v).toFixed(1));
    svgEl('path', {d: 'M' + x(0) + ',' + (height - 5) + 'L' + pts.join('L') + 'L' + x(n - 1) + ',' + (height - 5) + 'Z',
      fill: color, 'fill-opacity': 0.1}, svg);
    svgEl('path', {d: 'M' + pts.join('L'), fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round',
      'stroke-linecap': 'round'}, svg);
    svgEl('circle', {cx: x(n - 1), cy: y(values[n - 1]), r: 4, fill: color, stroke: SURFACE, 'stroke-width': 2}, svg);
  }

  // ── Legend & table ──
  // items: [{label, color, shape: 'rect'|'line', detail}]
  function legend(host, items) {
    clear(host);
    for (const it of items) {
      const li = htmlEl('span', 'legend-item', null, host);
      const key = htmlEl('span', 'legend-key ' + (it.shape || 'rect'), null, li);
      key.style.backgroundColor = it.color;
      htmlEl('span', null, it.label, li);
      if (it.detail) htmlEl('span', 'legend-detail', it.detail, li);
    }
  }

  // columns: [{label, num}], rows: [[cell text]]
  function table(host, columns, rows, caption) {
    clear(host);
    const wrap = htmlEl('div', 'table-card', null, host);
    const tbl = htmlEl('table', null, null, wrap);
    if (caption) htmlEl('caption', 'visually-hidden', caption, tbl);
    const head = htmlEl('tr', null, null, htmlEl('thead', null, null, tbl));
    columns.forEach(c => { const th = htmlEl('th', c.num ? 'num' : null, c.label, head); th.setAttribute('scope', 'col'); });
    const body = htmlEl('tbody', null, null, tbl);
    for (const r of rows) {
      const tr = htmlEl('tr', null, null, body);
      r.forEach((cell, i) => htmlEl(i === 0 ? 'th' : 'td', columns[i] && columns[i].num ? 'num' : null, cell, tr));
      if (tr.firstChild) tr.firstChild.setAttribute('scope', 'row');
    }
  }

  return {columns, lines, hbars, heatmap, sparkline, legend, table, hideTip, reset,
    fmtInt, fmtPct, fmtSigned, bucketLabel, htmlEl, clear};
})();
