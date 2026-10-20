// Small dependency-free chart renderers. All return html`` templates.
import { html, raw, fmtInt, toDate, isConnected, outcomeOf } from './util.js';

const TONE_COLOR = { ok: 'var(--ok)', sig: 'var(--signal)', bad: 'var(--bad)', warn: 'var(--warn)', dat: 'var(--data)', '': 'var(--fg-3)' };
export const toneColor = (tone) => TONE_COLOR[tone] ?? 'var(--data)';

export function sparkline(values, color = 'var(--data)') {
  if (!values.length || values.every((v) => !v)) return html`<svg class="spark" viewBox="0 0 100 34" aria-hidden="true"><line x1="0" y1="30" x2="100" y2="30" stroke="var(--line-2)" stroke-dasharray="3 3"/></svg>`;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? 100 / (values.length - 1) : 100;
  const pts = values.map((v, i) => [i * step, 30 - (v / max) * 26]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `0,32 ${line} 100,32`;
  const [lx, ly] = pts[pts.length - 1];
  return raw(`<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true">
    <polygon points="${area}" fill="${color}" opacity=".12"/>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
    <circle cx="${lx}" cy="${ly}" r="2.2" fill="${color}"/></svg>`);
}

/** items: [{label, value, tone?, display?}] */
export function hbars(items, { max } = {}) {
  const top = max ?? Math.max(...items.map((i) => i.value), 1);
  return html`<div class="hbars">${items.map((i) => html`
    <div class="hbar"><span class="lbl" title="${i.label}">${i.label}</span>
      <span class="track"><i style="width:${((i.value / top) * 100).toFixed(1)}%;background:${toneColor(i.tone ?? 'dat')}"></i></span>
      <span class="val">${i.display ?? fmtInt(i.value)}</span></div>`)}</div>`;
}

/** cols: [{label, value, tip?, segments?: [{value, color}]}] */
export function vbars(cols, { height = 160 } = {}) {
  const top = Math.max(...cols.map((c) => c.value), 1);
  return html`<div class="vbars" style="height:${height}px">${cols.map((c) => html`
    <div class="col"><span class="tip">${c.tip ?? fmtInt(c.value)}</span>
      <div class="bar" style="height:${Math.max(1.5, (c.value / top) * 100).toFixed(1)}%">${(c.segments || []).map((s) => html`<i style="height:${c.value ? ((s.value / c.value) * 100).toFixed(1) : 0}%;background:${s.color}"></i>`)}</div>
      <span>${c.label}</span></div>`)}</div>`;
}

/** Bucket calls into the last `days` calendar days (local time). */
export function callsByDay(calls, days = 14) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const buckets = Array.from({ length: days }, (_, i) => {
    const d = new Date(today); d.setDate(today.getDate() - (days - 1 - i));
    return { date: d, calls: [] };
  });
  const first = buckets[0].date.getTime();
  for (const call of calls) {
    const d = toDate(call.start_time || call.updated_at);
    if (!d) continue;
    const idx = Math.floor((new Date(d).setHours(0, 0, 0, 0) - first) / 86400000);
    if (idx >= 0 && idx < days) buckets[idx].calls.push(call);
  }
  return buckets;
}

/** Day-of-week × hour connect-rate matrix. Only hours that have calls are shown. */
export function heatmap(calls) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const grid = days.map(() => Array.from({ length: 24 }, () => ({ total: 0, connected: 0 })));
  for (const call of calls) {
    const d = toDate(call.start_time);
    if (!d) continue;
    const cell = grid[(d.getDay() + 6) % 7][d.getHours()];
    cell.total += 1;
    if (isConnected(call)) cell.connected += 1;
  }
  const hours = [];
  for (let h = 0; h < 24; h++) if (grid.some((row) => row[h].total)) hours.push(h);
  if (!hours.length) return null;
  const lo = Math.max(0, Math.min(...hours)), hi = Math.min(23, Math.max(...hours));
  const span = [];
  for (let h = lo; h <= hi; h++) span.push(h);
  return html`<div class="heat" style="--cols:${span.length}">
    <span></span>${span.map((h) => html`<span>${h}</span>`)}
    ${days.map((day, di) => html`<span style="text-align:left">${day}</span>${span.map((h) => {
      const c = grid[di][h];
      if (!c.total) return html`<i title="${day} ${h}:00 · no calls"></i>`;
      const rate = c.connected / c.total;
      return html`<i data-v style="--v:${(0.15 + rate * 0.85).toFixed(2)}" title="${day} ${h}:00 · ${Math.round(rate * 100)}% connected of ${c.total}"></i>`;
    })}`)}
  </div>`;
}

export function countBy(calls, fn) {
  const map = new Map();
  for (const c of calls) {
    const k = fn(c);
    if (k === null || k === undefined || k === '') continue;
    map.set(k, (map.get(k) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

export { outcomeOf };
