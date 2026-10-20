// NeuroCaller platform shell: workspace gate, navigation, hash router.
import { api } from './api.js';
import { workspace, loaders, startLive, stopLive, onLive } from './state.js';
import { html, mount, icon, LOGO_MARK, errorState, toggleTheme } from './util.js';

const SCREENS = {
  command: { title: 'Command center', sub: 'Your calling activity at a glance', load: () => import('./screens/command.js') },
  monitor: { title: 'Live monitor', sub: 'Calls happening right now', load: () => import('./screens/monitor.js') },
  campaigns: { title: 'Campaigns', sub: 'Progress and controls for every campaign', load: () => import('./screens/campaigns.js') },
  studio: { title: 'Agent studio', sub: 'Who your agent is and how it calls', load: () => import('./screens/studio.js') },
  copilot: { title: 'Copilot', sub: 'Chat with your agent to launch and manage calls', load: () => import('./screens/copilot.js') },
  calls: { title: 'Calls', sub: 'Recordings, transcripts, and outcomes', load: () => import('./screens/calls.js') },
  leads: { title: 'Leads', sub: 'Everyone you have called, by outcome', load: () => import('./screens/leads.js') },
  analytics: { title: 'Analytics', sub: 'What is working and when', load: () => import('./screens/analytics.js') },
  settings: { title: 'Settings', sub: 'Workspace, connections, and appearance', load: () => import('./screens/settings.js') },
};

const NAV = [
  ['Operate', [['command', 'grid'], ['monitor', 'pulse'], ['campaigns', 'target']]],
  ['Build', [['studio', 'bot'], ['copilot', 'spark']]],
  ['Understand', [['calls', 'phone'], ['leads', 'users'], ['analytics', 'chart']]],
];

const root = document.getElementById('root');
let current = { name: null, cleanup: null, token: 0 };

// ── Workspace gate ──────────────────────────────────────────────────────────
function renderGate(message = '') {
  stopLive();
  mount(root, html`
    <main class="gate">
      <form class="card" id="gateForm" novalidate>
        <span class="brand">${LOGO_MARK}NeuroCaller</span>
        <div style="display:grid;gap:6px">
          <h1>Connect your workspace</h1>
          <p class="muted" style="font-size:14px">Enter the organisation and user IDs your calls are recorded under. They stay in this browser.</p>
        </div>
        <div class="field"><label for="gOrg">Organisation ID</label><input class="input" id="gOrg" name="org" autocomplete="organization" placeholder="org_8f2c41" required></div>
        <div class="field"><label for="gUser">User ID</label><input class="input" id="gUser" name="user" autocomplete="username" placeholder="user_19ab07" required></div>
        <p class="err" id="gErr" role="alert">${message}</p>
        <button class="btn primary" type="submit">Open dashboard</button>
        <p class="faint" style="font-size:12.5px">Tip: link straight in with <span class="mono">/app?org_id=…&amp;user_id=…</span></p>
      </form>
    </main>`);
  const form = document.getElementById('gateForm');
  form.addEventListener('input', () => { document.getElementById('gErr').textContent = ''; });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const org = form.org.value.trim();
    const user = form.user.value.trim();
    if (!org || !user) {
      document.getElementById('gErr').textContent = 'Enter both IDs to continue.';
      return;
    }
    workspace.set(org, user);
    boot();
  });
  form.org.focus();
}

// ── Shell ───────────────────────────────────────────────────────────────────
function renderShell(ws) {
  mount(root, html`
    <div class="shell" id="shell">
      <aside class="side" aria-label="Main navigation">
        <a class="brand" href="/" title="NeuroCaller home">${LOGO_MARK}NeuroCaller</a>
        ${NAV.map(([group, items]) => html`
          <h5>${group}</h5>
          ${items.map(([key, ic]) => html`<a class="nav-item" href="#/${key}" data-nav="${key}">${icon(ic)}${SCREENS[key].title}${key === 'monitor' ? html`<span class="count" id="liveCount" hidden>0</span>` : ''}</a>`)}
        `)}
        <div class="side-foot">
          <a class="nav-item" href="#/settings" data-nav="settings">${icon('gear')}Settings</a>
          <div class="ws-card">
            <span class="faint">Workspace</span>
            <span class="id" title="${ws.org}">${ws.org}</span>
            <span class="id faint" title="${ws.user}">${ws.user}</span>
            <div class="row2"><span class="health" id="health"><i></i>Checking server</span><button class="btn ghost sm" id="switchWs" type="button">Switch</button></div>
          </div>
        </div>
      </aside>
      <div class="scrim" id="scrim"></div>
      <div class="main">
        <header class="topbar">
          <button class="iconbtn menu-btn" id="menuBtn" type="button" aria-label="Open navigation">${icon('menu')}</button>
          <div style="min-width:0"><h1 id="scrTitle">NeuroCaller</h1><p class="sub" id="scrSub"></p></div>
          <div class="actions">
            <span id="scrActions" style="display:contents"></span>
            <span class="live-pill hide-sm" id="livePill"><span class="live-dot"></span><span>Connecting</span></span>
            <button class="iconbtn" id="themeBtn" type="button" aria-label="Toggle light and dark theme">${icon('moon')}</button>
          </div>
        </header>
        <main class="screen" id="screen" tabindex="-1"></main>
      </div>
    </div>`);

  const shell = document.getElementById('shell');
  document.getElementById('themeBtn').addEventListener('click', toggleTheme);
  document.getElementById('switchWs').addEventListener('click', () => { workspace.clear(); renderGate(); });
  document.getElementById('menuBtn').addEventListener('click', () => shell.classList.add('nav-open'));
  document.getElementById('scrim').addEventListener('click', () => shell.classList.remove('nav-open'));
  shell.querySelectorAll('.side a').forEach((a) => a.addEventListener('click', () => shell.classList.remove('nav-open')));

  onLive((live) => {
    const pill = document.getElementById('livePill');
    if (!pill) return;
    const label = { live: 'Live feed on', connecting: 'Connecting', reconnecting: 'Reconnecting', idle: 'Offline' }[live.status] || live.status;
    pill.className = `live-pill hide-sm ${live.status}`;
    pill.lastElementChild.textContent = label;
    const active = [...live.calls.values()].filter((c) => !c.hangup_cause).length;
    const count = document.getElementById('liveCount');
    if (count) { count.hidden = !active; count.textContent = active; }
  });

  api.health()
    .then(() => setHealth('ok', 'Server connected'))
    .catch(() => setHealth('bad', 'Server issue'));
}

function setHealth(tone, text) {
  const el = document.getElementById('health');
  if (!el) return;
  el.className = `health ${tone}`;
  el.lastChild.textContent = text;
}

// ── Router ──────────────────────────────────────────────────────────────────
function parseHash() {
  const parts = window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  const name = SCREENS[parts[0]] ? parts[0] : 'command';
  return { name, params: parts.slice(1) };
}

async function route(ws) {
  const { name, params } = parseHash();
  const meta = SCREENS[name];
  const token = ++current.token;

  try { current.cleanup?.(); } catch { /* ignore */ }
  current = { name, cleanup: null, token };

  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  document.getElementById('scrTitle').textContent = meta.title;
  document.getElementById('scrSub').textContent = meta.sub;
  document.getElementById('scrActions').innerHTML = '';
  document.title = `${meta.title} · NeuroCaller`;

  const screen = document.getElementById('screen');
  screen.innerHTML = '';
  window.scrollTo(0, 0);

  const ctx = {
    ws,
    load: loaders(ws),
    params,
    go: (path) => { window.location.hash = path; },
    setTitle: (title, sub) => {
      document.getElementById('scrTitle').textContent = title;
      if (sub !== undefined) document.getElementById('scrSub').textContent = sub;
      document.title = `${title} · NeuroCaller`;
    },
    setActions: (template) => mount(document.getElementById('scrActions'), template),
    isCurrent: () => current.token === token,
    rerender: () => route(ws),
  };

  try {
    const mod = await meta.load();
    if (current.token !== token) return;
    const cleanup = await mod.render(screen, ctx);
    if (current.token !== token) { cleanup?.(); return; }
    current.cleanup = cleanup || null;
  } catch (err) {
    console.error(err);
    if (current.token === token) {
      mount(screen, errorState(err, 'Reload screen'));
      screen.querySelector('[data-retry]')?.addEventListener('click', () => route(ws));
    }
  }
}

// ── Boot ────────────────────────────────────────────────────────────────────
function boot() {
  const ws = workspace.get();
  if (!ws) { renderGate(); return; }
  renderShell(ws);
  startLive(ws);
  window.onhashchange = () => route(ws);
  route(ws);
}

boot();
