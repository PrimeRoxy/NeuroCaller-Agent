import { api } from '../api.js';
import { workspace } from '../state.js';
import { html, mount, icon, titleCase, toggleTheme, copyText, store } from '../util.js';

export async function render(el, ctx) {
  mount(el, html`
    <div class="g">
      <section class="card s6"><div class="card-h"><h3>Workspace</h3></div>
        <form id="wsForm" class="form-grid" style="grid-template-columns:minmax(0,1fr)" novalidate>
          <div class="field"><label for="sOrg">Organisation ID</label><div style="display:flex;gap:8px"><input class="input mono" id="sOrg" value="${ctx.ws.org}" required><button class="iconbtn" type="button" data-copy="${ctx.ws.org}" aria-label="Copy organisation ID">${icon('copy')}</button></div></div>
          <div class="field"><label for="sUser">User ID</label><div style="display:flex;gap:8px"><input class="input mono" id="sUser" value="${ctx.ws.user}" required><button class="iconbtn" type="button" data-copy="${ctx.ws.user}" aria-label="Copy user ID">${icon('copy')}</button></div></div>
          <p class="err" id="wsErr" role="alert"></p>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary sm" type="submit">Switch workspace</button><button class="btn sm" type="button" id="signOut">${icon('logout')}Disconnect</button></div>
          <p class="faint" style="font-size:12.5px">Share a direct link: <span class="mono" id="deepLink" style="overflow-wrap:anywhere"></span> <button class="btn ghost sm" type="button" id="copyLink">${icon('copy')}Copy</button></p>
        </form>
      </section>
      <section class="card s6"><div class="card-h"><h3>Server</h3><button class="btn ghost sm" id="recheck">${icon('refresh')}Check again</button></div><div id="health"></div></section>
      <section class="card s6"><div class="card-h"><h3>Appearance</h3></div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><span><b style="font-size:14px">Theme</b><br><span class="faint" style="font-size:13px" id="themeLabel"></span></span><button class="btn sm" id="theme">${icon('moon')}Switch theme</button></div>
      </section>
      <section class="card s6"><div class="card-h"><h3>Developers</h3></div>
        <p class="muted" style="font-size:13.5px;margin-bottom:12px">Every screen in this app runs on the same public API. Browse and try the endpoints in the interactive docs.</p>
        <a class="btn sm" href="/docs" target="_blank" rel="noopener">Open API docs</a>
      </section>
    </div>`);

  const $ = (s) => el.querySelector(s);
  const link = `${window.location.origin}/app?org_id=${encodeURIComponent(ctx.ws.org)}&user_id=${encodeURIComponent(ctx.ws.user)}`;
  $('#deepLink').textContent = link;
  $('#copyLink').addEventListener('click', () => copyText(link));
  el.addEventListener('click', (e) => { const c = e.target.closest('[data-copy]'); if (c) copyText(c.dataset.copy); });

  $('#wsForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const org = $('#sOrg').value.trim();
    const user = $('#sUser').value.trim();
    if (!org || !user) { $('#wsErr').textContent = 'Enter both IDs.'; return; }
    workspace.clear();
    workspace.set(org, user);
    window.location.hash = '#/command';
    window.location.reload();
  });
  $('#signOut').addEventListener('click', () => {
    workspace.clear();
    store(`nc.chat.${ctx.ws.org}.${ctx.ws.user}`, null);
    window.location.hash = '';
    window.location.reload();
  });

  const themeLabel = () => { $('#themeLabel').textContent = document.documentElement.dataset.theme === 'light' ? 'Light' : 'Dark'; };
  themeLabel();
  $('#theme').addEventListener('click', () => { toggleTheme(); themeLabel(); });

  const check = async () => {
    mount($('#health'), html`<p class="faint" style="font-size:13px">Checking…</p>`);
    try {
      const res = await api.health();
      const services = res?.services || {};
      mount($('#health'), html`<dl class="kv">
        <dt>Status</dt><dd><span class="chip ok">${titleCase(res?.status || 'healthy')}</span></dd>
        ${Object.entries(services).map(([k, v]) => html`<dt>${titleCase(k)}</dt><dd><span class="chip ${String(v).startsWith('connected') ? 'ok' : String(v).startsWith('error') ? 'bad' : ''}">${titleCase(String(v))}</span></dd>`)}
      </dl>`);
    } catch (err) {
      const detail = err.message;
      mount($('#health'), html`<div class="state error"><b>Server needs attention</b><p>${detail}</p></div>`);
    }
  };
  $('#recheck').addEventListener('click', check);
  check();
}
