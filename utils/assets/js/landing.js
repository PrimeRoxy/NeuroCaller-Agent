import { api } from './api.js';
import { html, mount, icon, toggleTheme, fmtInt } from './util.js';

const $ = (s) => document.querySelector(s);
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

$('#themeBtn').addEventListener('click', toggleTheme);

// ── Rotating headline ───────────────────────────────────────────────────────
const words = ['qualifies leads', 'handles objections', 'books follow-ups', 'never forgets a callback'];
let wi = 0;
const rot = $('#rot');
if (!reduce) {
  setInterval(() => {
    rot.classList.add('out');
    setTimeout(() => { wi = (wi + 1) % words.length; rot.textContent = words[wi]; rot.classList.remove('out'); }, 260);
  }, 2600);
}

// ── Waveform ────────────────────────────────────────────────────────────────
const canvas = $('#wave');
const ctx2d = canvas.getContext('2d');
let speaker = 'ai';
let amp = 1;
const sizeCanvas = () => {
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, r.width * devicePixelRatio);
  canvas.height = Math.max(1, r.height * devicePixelRatio);
};
sizeCanvas();
window.addEventListener('resize', sizeCanvas);
function drawWave(t) {
  const w = canvas.width, h = canvas.height, n = 64, gap = w / n;
  ctx2d.clearRect(0, 0, w, h);
  ctx2d.fillStyle = speaker === 'ai' ? css('--signal') : css('--data');
  for (let i = 0; i < n; i++) {
    const e = Math.sin(i * 0.45 + t / 180) * 0.5 + Math.sin(i * 0.17 - t / 260) * 0.35 + Math.sin(i * 1.3 + t / 90) * 0.15;
    const bh = Math.max(3 * devicePixelRatio, (Math.abs(e) * 0.9 + 0.08) * h * amp);
    ctx2d.globalAlpha = 0.35 + 0.65 * Math.abs(e);
    const x = i * gap + gap * 0.25, bw = gap * 0.5;
    ctx2d.beginPath();
    if (ctx2d.roundRect) ctx2d.roundRect(x, (h - bh) / 2, bw, bh, bw / 2); else ctx2d.rect(x, (h - bh) / 2, bw, bh);
    ctx2d.fill();
  }
  ctx2d.globalAlpha = 1;
  if (!reduce) requestAnimationFrame(drawWave);
}
requestAnimationFrame(drawWave);

// ── Sample call (scripted, clearly labelled as a sample in the UI) ──────────
const script = [
  { s: 'ai', who: 'Agent', txt: "Hi Priya, it's Aria. You asked about the 3-bedroom flat on Park Road yesterday. Is now a good time?", st: 0 },
  { s: 'lead', who: 'Priya', txt: 'Yes, quickly. We are looking to move in about three months.', st: 1 },
  { s: 'ai', who: 'Agent', txt: 'Great. Is it for your family, and do you need parking for one car or two?', st: 1 },
  { s: 'lead', who: 'Priya', txt: 'Family of four, two cars. What is the price?', st: 2 },
  { s: 'ai', who: 'Agent', txt: 'It has two covered spots. The agent can share the full price breakdown at a viewing.', st: 2 },
  { s: 'lead', who: 'Priya', txt: "I'm busy this week. Call me back Saturday morning?", st: 3 },
  { s: 'ai', who: 'Agent', txt: "Of course. I'll call you Saturday at 10. Shall I also send the floor plan on WhatsApp?", st: 4 },
  { s: 'lead', who: 'Priya', txt: 'Yes please. Saturday at 10 works.', st: 4 },
];
const tx = $('#tx');
const stages = [...document.querySelectorAll('#stages span')];
let idx = 0, timer = null, clock = null, sec = 8;

const setStage = (n) => stages.forEach((el, i) => { el.className = i < n ? 'done' : i === n ? 'now' : ''; });
function addLine(line) {
  const row = document.createElement('div');
  row.className = `ln${line.s === 'ai' ? ' ai' : ''}`;
  const b = document.createElement('b'); b.textContent = line.who;
  const span = document.createElement('span'); span.textContent = line.txt;
  row.append(b, span);
  tx.appendChild(row);
  while (tx.children.length > 6) tx.removeChild(tx.firstChild);
  speaker = line.s;
  setStage(line.st);
}
function step() {
  if (idx >= script.length) return;
  addLine(script[idx++]);
  if (idx === script.length) {
    $('#outcome').classList.add('on');
    $('#outcomeTxt').textContent = 'Follow-up scheduled · Saturday 10:00';
    amp = 0.25;
    clearInterval(clock);
    timer = setTimeout(() => startCall(false), 6000);
  } else {
    timer = setTimeout(step, 2300);
  }
}
function startCall(fromClick) {
  clearTimeout(timer); clearInterval(clock);
  tx.innerHTML = ''; idx = 0; amp = 1; sec = fromClick ? 0 : 8;
  $('#outcome').classList.remove('on');
  $('#outcomeTxt').textContent = 'Waiting for outcome';
  clock = setInterval(() => { sec += 1; $('#callTime').textContent = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`; }, 1000);
  addLine(script[0]); addLine(script[1]); idx = 2;
  if (!reduce) timer = setTimeout(step, 1800);
}
$('#playCall').addEventListener('click', () => startCall(true));
startCall(false);

// ── Use cases ───────────────────────────────────────────────────────────────
const cases = {
  auto: ['Follow up on every test-drive enquiry', "Reach every website and showroom lead the same day, find out what they're shopping for, and book the visit.", 'Hi, this is Sakshi from the dealership. You asked about a new SUV yesterday. Are you planning to buy soon, or upgrading your current car?'],
  re: ['Call every property enquiry while it is still warm', 'Qualify budget, timeline, and must-haves, then book a viewing or a callback at the time the buyer picks.', "Hi Maria, you asked about the 3-bedroom on Elm Street. Are you looking to move in the next three months? I can set up a viewing this Saturday."],
  saas: ['Turn sign-ups into booked demos', 'Call new trial users within minutes, learn their use case, and hand qualified accounts to your sales team.', "Hi Alex, you started a trial yesterday. What made you look for a tool like this? I can set up a 20-minute walkthrough with our team."],
  edu: ['Follow up with every course applicant', 'Answer questions about fees and start dates, and book counsellor calls for serious applicants.', 'Hi Riya, you started an application for the data science programme. Do you have any questions about fees or the start date?'],
};
$('#ucTabs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-uc]');
  if (!b) return;
  document.querySelectorAll('#ucTabs button').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
  const [title, body, line] = cases[b.dataset.uc];
  $('#ucTitle').textContent = title; $('#ucBody').textContent = body; $('#ucScript').textContent = line;
});

// ── Voices (live from the API) ──────────────────────────────────────────────
const audio = new Audio();
let playing = null;
let waveTimer = null;
const stopVoice = () => {
  audio.pause();
  clearInterval(waveTimer);
  if (playing) {
    playing.classList.remove('on');
    playing.querySelector('.pb').innerHTML = icon('play').value;
    playing.querySelectorAll('.mini-wave i').forEach((i) => { i.style.height = '4px'; });
  }
  playing = null;
};
audio.addEventListener('ended', stopVoice);

async function loadVoices(country) {
  const grid = $('#voiceGrid');
  stopVoice();
  try {
    const voices = await api.voices(country || undefined);
    if (!voices.length) { grid.innerHTML = '<p class="faint">No voices available right now.</p>'; return; }
    mount(grid, voices.map((v) => html`
      <button class="voice" type="button" data-src="${v.audio_url}" aria-label="Play ${v.name}, ${v.gender} voice">
        <span class="pb">${icon('play')}</span>
        <span><b>${v.name}</b><span class="g">${v.gender}</span></span>
        <span class="mini-wave" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span>
      </button>`));
  } catch {
    grid.innerHTML = '<p class="faint">Voices are unavailable right now. Try again in a moment.</p>';
  }
}
$('#voiceGrid').addEventListener('click', (e) => {
  const card = e.target.closest('.voice');
  if (!card) return;
  if (playing === card) { stopVoice(); return; }
  stopVoice();
  audio.src = card.dataset.src;
  audio.play().then(() => {
    playing = card;
    card.classList.add('on');
    card.querySelector('.pb').innerHTML = icon('pause').value;
    if (!reduce) waveTimer = setInterval(() => card.querySelectorAll('.mini-wave i').forEach((i) => { i.style.height = `${4 + Math.random() * 14}px`; }), 140);
  }).catch(() => { card.querySelector('.g').textContent = "couldn't play sample"; });
});
$('#vTabs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-v]');
  if (!b) return;
  document.querySelectorAll('#vTabs button').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
  loadVoices(b.dataset.v);
});
loadVoices('IN');

Promise.all([api.voices('IN').catch(() => []), api.voices().catch(() => [])]).then(([india, global]) => {
  const total = new Set([...india, ...global].map((v) => v.name)).size;
  if (total) $('#voiceCount').textContent = fmtInt(total);
});

// ── Estimate ────────────────────────────────────────────────────────────────
function estimate() {
  const calls = Number($('#rCalls').value);
  const conn = Number($('#rConn').value) / 100;
  const qual = Number($('#rQual').value) / 100;
  const mins = Number($('#rMin').value);
  $('#rCallsO').textContent = fmtInt(calls);
  $('#rConnO').textContent = `${Math.round(conn * 100)}%`;
  $('#rQualO').textContent = `${Math.round(qual * 100)}%`;
  $('#rMinO').textContent = `${mins} min`;
  const monthly = calls * 22;
  $('#oQual').textContent = fmtInt(monthly * conn * qual);
  $('#oHrs').textContent = fmtInt((monthly * mins) / 60);
  $('#oConv').textContent = fmtInt(monthly * conn);
}
['#rCalls', '#rConn', '#rQual', '#rMin'].forEach((s) => $(s).addEventListener('input', estimate));
estimate();
