// Shared helpers: safe HTML templating, formatting, small UI utilities.

const RAW = Symbol('raw');

export function esc(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** Mark a string as trusted HTML. Only use for markup built in this codebase. */
export const raw = (value) => ({ [RAW]: true, value: String(value) });

function renderValue(value) {
  if (value === null || value === undefined || value === false) return '';
  if (Array.isArray(value)) return value.map(renderValue).join('');
  if (typeof value === 'object' && value[RAW]) return value.value;
  return esc(value);
}

/** Tagged template that escapes every interpolation unless it came from html`` or raw(). */
export function html(strings, ...values) {
  let out = '';
  strings.forEach((part, i) => {
    out += part;
    if (i < values.length) out += renderValue(values[i]);
  });
  return raw(out);
}

export function mount(el, template) {
  el.innerHTML = renderValue(template);
}

// ── Dates ───────────────────────────────────────────────────────────────────
// The backend stores naive UTC datetimes; treat timestamps without a zone as UTC.
export function toDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') return new Date(value > 1e12 ? value : value * 1000);
  const s = String(value);
  const hasZone = /[zZ]$|[+-]\d\d:?\d\d$/.test(s);
  const d = new Date(hasZone || !s.includes('T') ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const dtFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const dFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
const tFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });

export const fmtDateTime = (v) => { const d = toDate(v); return d ? dtFmt.format(d) : '—'; };
export const fmtDate = (v) => { const d = toDate(v); return d ? dFmt.format(d) : '—'; };
export const fmtTime = (v) => { const d = toDate(v); return d ? tFmt.format(d) : '—'; };

export function relTime(value) {
  const d = toDate(value);
  if (!d) return '—';
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  const abs = Math.abs(sec);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (abs < 45) return 'just now';
  if (abs < 3600) return rtf.format(-Math.round(sec / 60), 'minute');
  if (abs < 86400) return rtf.format(-Math.round(sec / 3600), 'hour');
  if (abs < 86400 * 30) return rtf.format(-Math.round(sec / 86400), 'day');
  return fmtDate(d);
}

// ── Numbers ─────────────────────────────────────────────────────────────────
const intFmt = new Intl.NumberFormat();
export const fmtInt = (n) => intFmt.format(Math.round(Number(n) || 0));
export const fmtPct = (n, digits = 1) => `${(Number(n) || 0).toFixed(digits).replace(/\.0+$/, '')}%`;
export const pct = (part, whole) => (whole ? (part / whole) * 100 : 0);
export const fmtUsd = (n) => (n === null || n === undefined || Number.isNaN(Number(n)) ? '—' : `$${Number(n).toFixed(Number(n) < 1 ? 3 : 2)}`);

export function fmtDuration(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (!s) return '0s';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${String(r).padStart(2, '0')}s`;
  return `${r}s`;
}

export function fmtMinutes(minutes) {
  const m = Number(minutes) || 0;
  if (m >= 60) return `${(m / 60).toFixed(1).replace(/\.0$/, '')}h`;
  return `${m.toFixed(m < 10 ? 1 : 0).replace(/\.0$/, '')}m`;
}

export function fmtPhone(value) {
  if (!value) return '—';
  const s = String(value).trim();
  const digits = s.replace(/[^\d]/g, '');
  if (!digits) return s;
  // Only add "+" when the number already carries a country code; never guess one for local numbers.
  if (s.startsWith('+')) return s;
  return digits.length > 10 ? `+${digits}` : digits;
}

export function initials(name, fallback = '') {
  const source = (name || '').trim();
  if (source) {
    const parts = source.split(/\s+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '#';
  }
  const digits = String(fallback).replace(/\D/g, '');
  return digits ? digits.slice(-2) : '#';
}

export const shortId = (id) => (id ? String(id).slice(0, 8) : '—');

/** Campaign IDs end in their UTC start time (org:user:20260812T074713Z); show that instead of the raw ID. */
export function campaignLabel(id) {
  if (!id) return '—';
  const m = String(id).match(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z?$/);
  if (!m) return shortId(id);
  return `Campaign ${dtFmt.format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])))}`;
}

// ── Domain mapping ──────────────────────────────────────────────────────────
export const OUTCOMES = ['Qualified', 'Follow-up required', 'Not interested', 'Unreachable', 'Unclassified'];
// Plivo reports NORMAL_CLEARING / Normal Hangup; VoiceLink reports ANSWER for answered calls.
const SUCCESS_HANGUPS = new Set(['Normal Hangup', 'NORMAL_CLEARING', 'ANSWER', 'ANSWERED']);

export const isConnected = (call) => SUCCESS_HANGUPS.has(call?.hangup_cause);
export const isLive = (call) => !call?.hangup_cause;
/** Outcome from post-call analysis; falls back to the live lead status when it uses the same labels. */
export const outcomeOf = (call) => call?.outcome || (OUTCOMES.includes(call?.lead_status) ? call.lead_status : 'Unclassified');

/** Call length in seconds. VoiceLink leaves `duration` at 0 and stores `total_duration` instead. */
export function callDuration(call) {
  const direct = Number(call?.duration) || Number(call?.total_duration) || Number(call?.recording_duration);
  if (direct) return direct;
  const start = toDate(call?.start_time), end = toDate(call?.end_time);
  const diff = start && end ? (end - start) / 1000 : 0;
  return diff > 0 && diff < 6 * 3600 ? diff : 0;
}

export function outcomeTone(outcome) {
  switch (outcome) {
    case 'Qualified': return 'ok';
    case 'Follow-up required': return 'sig';
    case 'Not interested': return 'bad';
    case 'Unreachable': return 'warn';
    default: return '';
  }
}

export function campaignTone(status) {
  switch (status) {
    case 'running': return 'sig';
    case 'paused': return 'warn';
    case 'completed': return 'ok';
    case 'failed': return 'bad';
    case 'cancelled': return '';
    default: return 'dat';
  }
}

export const titleCase = (s) => String(s || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export function callCost(call) {
  const price = call?.analysis_token_metrics?.total_price?.price;
  return typeof price === 'number' ? price : null;
}

/** Split "Agent: …\nCustomer: …" transcripts into turns. Accepts strings or message arrays. */
export function parseTranscript(transcript) {
  if (!transcript) return [];
  if (Array.isArray(transcript)) {
    return transcript
      .map((m) => {
        const role = String(m.role || m.speaker || '').toLowerCase();
        const text = m.text || m.content || m.message || '';
        const speaker = /agent|assistant|model|bot/.test(role) ? 'agent' : /customer|user|lead/.test(role) ? 'customer' : 'other';
        return { speaker, label: speaker === 'agent' ? 'Agent' : speaker === 'customer' ? 'Customer' : titleCase(role || 'Note'), text: String(text) };
      })
      .filter((t) => t.text.trim());
  }
  const turns = [];
  for (const line of String(transcript).split(/\r?\n/)) {
    const trimmed = line.replace(/\*\*/g, '').trim();
    if (!trimmed) continue;
    const match = trimmed.match(/^(agent|customer|assistant|user|lead|bot)\s*:\s*(.*)$/i);
    if (match) {
      const role = match[1].toLowerCase();
      const speaker = ['agent', 'assistant', 'bot'].includes(role) ? 'agent' : 'customer';
      turns.push({ speaker, label: speaker === 'agent' ? 'Agent' : 'Customer', text: match[2] });
    } else if (turns.length) {
      turns[turns.length - 1].text += ` ${trimmed}`;
    } else {
      turns.push({ speaker: 'other', label: 'Note', text: trimmed });
    }
  }
  return turns;
}

// ── Storage (best effort) ───────────────────────────────────────────────────
export function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(key));
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch { /* storage unavailable */ }
  return undefined;
}

export function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  store('nc.theme', next);
}

// ── In-memory cache with TTL ────────────────────────────────────────────────
const cacheMap = new Map();
export function cached(key, ttlMs, loader) {
  const hit = cacheMap.get(key);
  if (hit && Date.now() - hit.at < ttlMs) return hit.promise;
  const promise = loader();
  cacheMap.set(key, { at: Date.now(), promise });
  promise.catch(() => cacheMap.delete(key));
  return promise;
}
export function invalidate(prefix = '') {
  for (const key of cacheMap.keys()) if (key.startsWith(prefix)) cacheMap.delete(key);
}

// ── UI bits ─────────────────────────────────────────────────────────────────
export function toast(message, tone = '') {
  let host = document.querySelector('.toasts');
  if (!host) {
    host = document.createElement('div');
    host.className = 'toasts';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

export function debounce(fn, ms = 200) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function downloadCsv(filename, rows) {
  const cell = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const csv = rows.map((r) => r.map(cell).join(',')).join('\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied', 'ok'); }
  catch { toast("Couldn't copy. Select the text instead.", 'bad'); }
}

export function loadingState(rows = 3) {
  return html`<div class="skeleton" aria-busy="true" aria-label="Loading">${Array.from({ length: rows }, () => html`<i></i>`)}</div>`;
}

export function errorState(err, retryLabel = 'Try again') {
  return html`<div class="state error"><b>Couldn't load this</b><p>${err?.message || String(err)}</p><button class="btn sm" data-retry>${retryLabel}</button></div>`;
}

export function emptyState(title, body, action = '') {
  return html`<div class="state"><b>${title}</b><p>${body}</p>${action}</div>`;
}

// ── Icons (outline, 24px grid) ──────────────────────────────────────────────
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  pulse: '<path d="M3 12h3l3-7 4 14 3-7h5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  bot: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M9 13h.01M15 13h.01"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
  spark: '<path d="M12 3l1.9 5.8L20 10l-5 3.9L16.8 20 12 16.6 7.2 20 9 13.9 4 10l6.1-1.2z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
  play: '<path d="M7 4v16l13-8z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M7 4h4v16H7zM13 4h4v16h-4z" fill="currentColor" stroke="none"/>',
  send: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  clip: '<path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
};

export function icon(name, extra = '') {
  return raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${ICONS[name] || ''}</svg>`);
}

export const LOGO_MARK = raw('<span class="mark"><svg viewBox="0 0 16 16" fill="none" stroke="var(--on-signal)" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M2 8h2l1.5-4 2.5 8 2-6 1.5 2H14"/></svg></span>');
