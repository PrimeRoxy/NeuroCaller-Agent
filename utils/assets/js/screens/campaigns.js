import { api } from '../api.js';
import {
  html, mount, icon, fmtInt, fmtPhone, fmtDateTime, fmtDuration, callDuration, relTime, pct, titleCase, campaignLabel,
  campaignTone, outcomeOf, outcomeTone, loadingState, errorState, emptyState, toast, invalidate, copyText,
} from '../util.js';

const FILTERS = [['', 'All'], ['running', 'Running'], ['paused', 'Paused'], ['completed', 'Completed'], ['cancelled', 'Cancelled'], ['failed', 'Failed']];
const ACTIVE = new Set(['created', 'validating', 'generating_prompt', 'running', 'paused']);

export async function render(el, ctx) {
  if (ctx.params[0]) return renderDetail(el, ctx, ctx.params[0]);

  ctx.setActions(html`<a class="btn sm primary" href="#/copilot">${icon('plus')}New campaign</a>`);
  let filter = '';

  mount(el, html`
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Filter by status">${FILTERS.map(([v, l]) => html`<button type="button" data-f="${v}" aria-pressed="${v === filter}">${l}</button>`)}</div>
      <span class="faint" style="font-size:12.5px;margin-left:auto">New campaigns start from a Copilot chat</span>
    </div>
    <section class="card" style="padding:0"><div id="list" style="padding:16px">${loadingState(5)}</div></section>`);

  const list = el.querySelector('#list');

  const draw = async (fresh = false) => {
    try {
      const all = await ctx.load.campaigns(fresh);
      if (!ctx.isCurrent()) return;
      const rows = filter ? all.filter((c) => c.status === filter) : all;
      if (!rows.length) {
        mount(list, filter
          ? emptyState(`No ${titleCase(filter).toLowerCase()} campaigns`, 'Try another filter.')
          : emptyState('Start your first campaign', 'Tell Copilot who to call (paste numbers or upload a sheet) and it sets up the campaign for you.', html`<a class="btn sm primary" href="#/copilot">Open Copilot</a>`));
        return;
      }
      list.style.padding = '0';
      mount(list, html`<div class="tscroll"><table class="t">
        <thead><tr><th>Campaign</th><th>Status</th><th style="min-width:180px">Progress</th><th>Done</th><th>Failed</th><th>Pending</th><th>Callbacks</th><th>Caller ID</th><th>Created</th></tr></thead>
        <tbody>${rows.map((c) => {
          const total = c.total_numbers || c.total || 0;
          return html`<tr class="link" data-id="${c.campaign_id}" tabindex="0">
            <td><b>${campaignLabel(c.campaign_id)}</b></td>
            <td><span class="chip ${campaignTone(c.status)}">${c.status === 'running' ? html`<span class="live-dot"></span>` : ''}${titleCase(c.status)}</span></td>
            <td><div style="display:grid;gap:5px"><span class="prog"><i class="ok" style="width:${pct(c.completed || 0, total)}%"></i><i class="bad" style="width:${pct(c.failed || 0, total)}%"></i><i class="live" style="width:${pct(c.in_progress || 0, total)}%"></i></span><span class="faint mono" style="font-size:11.5px">${fmtInt((c.completed || 0) + (c.failed || 0))} of ${fmtInt(total)}</span></div></td>
            <td class="mono">${fmtInt(c.completed)}</td><td class="mono">${fmtInt(c.failed)}</td><td class="mono">${fmtInt(c.pending)}</td>
            <td class="mono">${fmtInt(c.callbacks_completed)} / ${fmtInt(c.callbacks_scheduled)}</td>
            <td class="mono">${c.plivo_number ? fmtPhone(c.plivo_number) : '—'}</td>
            <td class="faint">${fmtDateTime(c.created_at)}</td></tr>`;
        })}</tbody></table></div>`);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      list.style.padding = '16px';
      mount(list, errorState(err));
      list.querySelector('[data-retry]')?.addEventListener('click', () => draw(true));
    }
  };

  el.querySelector('.seg').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-f]');
    if (!b) return;
    filter = b.dataset.f;
    el.querySelectorAll('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    draw();
  });
  list.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-id]'); if (tr) ctx.go(`/campaigns/${encodeURIComponent(tr.dataset.id)}`); });
  list.addEventListener('keydown', (e) => { const tr = e.target.closest('tr[data-id]'); if (tr && e.key === 'Enter') ctx.go(`/campaigns/${encodeURIComponent(tr.dataset.id)}`); });

  await draw();
  const poll = setInterval(() => draw(true), 15000);
  return () => clearInterval(poll);
}

async function renderDetail(el, ctx, id) {
  ctx.setTitle(campaignLabel(id), 'Live progress, calls, and history');
  mount(el, html`
    <a class="back-link" href="#/campaigns">${icon('back')}All campaigns</a>
    <section class="card" id="head">${loadingState(3)}</section>
    <div class="g">
      <section class="card s8" style="padding:0"><div class="card-h" style="padding:16px 16px 0"><h3>Calls in this campaign</h3><span class="faint" id="callCount"></span></div><div id="calls" style="padding:16px">${loadingState(5)}</div></section>
      <div class="s4" style="display:grid;gap:16px;align-content:start">
        <section class="card"><div class="card-h"><h3>Scheduled callbacks</h3></div><div id="sched">${loadingState(2)}</div></section>
        <section class="card"><div class="card-h"><h3>Activity</h3></div><div id="events">${loadingState(4)}</div></section>
      </div>
    </div>`);

  const $ = (sel) => el.querySelector(sel);
  let campaign = null;

  const drawHead = (c, progress) => {
    const p = { ...c, ...(progress || {}) };
    const total = p.total_numbers || p.total || 0;
    const done = (p.completed || 0) + (p.failed || 0);
    const status = p.status;
    const actions = [];
    if (status === 'running') actions.push(html`<button class="btn sm" data-act="pause">Pause</button>`);
    if (status === 'paused') actions.push(html`<button class="btn sm primary" data-act="resume">Resume</button>`);
    if (status && !['completed', 'cancelled', 'failed'].includes(status)) actions.push(html`<button class="btn sm danger" data-act="cancel">Cancel campaign</button>`);
    mount($('#head'), html`
      <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <span class="chip ${campaignTone(status)}">${status === 'running' ? html`<span class="live-dot"></span>` : ''}${titleCase(status)}</span>
          <button class="btn ghost sm mono" data-copy="${id}" title="Copy campaign ID">${id} ${icon('copy')}</button>
        </div>
        <div style="display:flex;gap:8px" id="acts">${actions}</div>
      </div>
      <div id="confirmArea"></div>
      <div style="display:grid;gap:8px;margin-top:16px">
        <div style="display:flex;justify-content:space-between;font-size:13px"><span class="muted">${fmtInt(done)} of ${fmtInt(total)} numbers called</span><b class="mono">${Math.round(pct(done, total))}%</b></div>
        <span class="prog" style="height:10px"><i class="ok" style="width:${pct(p.completed || 0, total)}%"></i><i class="bad" style="width:${pct(p.failed || 0, total)}%"></i><i class="live" style="width:${pct(p.in_progress || 0, total)}%"></i></span>
      </div>
      <div class="kpis" style="margin-top:16px;grid-template-columns:repeat(auto-fit,minmax(120px,1fr))">
        ${[['Completed', p.completed, 'var(--ok)'], ['Failed', p.failed, 'var(--bad)'], ['In progress', p.in_progress, 'var(--data)'], ['Pending', p.pending, 'var(--fg-2)'], ['Callbacks', `${fmtInt(p.callbacks_completed)} / ${fmtInt(p.callbacks_scheduled)}`, 'var(--signal)']].map(([l, v, col]) => html`
          <div style="background:var(--bg-3);border-radius:10px;padding:10px 12px"><small class="faint" style="font-size:12px">${l}</small><div class="mono" style="font-size:20px;color:${col}">${typeof v === 'number' || v === undefined ? fmtInt(v) : v}</div></div>`)}
      </div>
      <dl class="kv" style="margin-top:16px">
        <dt>Created</dt><dd>${fmtDateTime(c.created_at)}</dd>
        <dt>Started</dt><dd>${fmtDateTime(c.started_at)}</dd>
        ${c.completed_at ? html`<dt>Finished</dt><dd>${fmtDateTime(c.completed_at)}</dd>` : ''}
        <dt>Caller ID</dt><dd class="mono">${c.plivo_number ? fmtPhone(c.plivo_number) : '—'}</dd>
        ${c.prompt_source ? html`<dt>Script source</dt><dd>${titleCase(c.prompt_source)}</dd>` : ''}
      </dl>`);
  };

  const loadHead = async () => {
    try {
      const [c, progress] = await Promise.all([api.campaign(id), api.campaignProgress(id).catch(() => null)]);
      if (!ctx.isCurrent()) return;
      campaign = c;
      drawHead(c, progress);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      mount($('#head'), errorState(err));
      $('#head [data-retry]')?.addEventListener('click', loadHead);
    }
  };

  $('#head').addEventListener('click', async (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) { copyText(copy.dataset.copy); return; }
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    if (act === 'cancel') {
      mount($('#confirmArea'), html`<div class="confirm" style="margin-top:12px;border-color:color-mix(in srgb,var(--bad) 50%,var(--line-2));background:var(--bad-soft)"><span style="flex:1">Cancel this campaign? Pending numbers won't be called. Calls already in progress finish normally.</span><button class="btn sm danger" data-act="cancel-yes">Cancel campaign</button><button class="btn sm" data-act="cancel-no">Keep running</button></div>`);
      return;
    }
    if (act === 'cancel-no') { $('#confirmArea').innerHTML = ''; return; }
    const endpoint = act === 'cancel-yes' ? 'cancel' : act;
    b.disabled = true;
    try {
      await api.campaignAction(id, endpoint);
      invalidate('campaigns');
      toast({ pause: 'Campaign paused', resume: 'Campaign resumed', cancel: 'Campaign cancelled' }[endpoint], 'ok');
      await loadHead();
    } catch (err) {
      toast(err.message, 'bad');
      b.disabled = false;
    }
  });

  const loadCalls = async () => {
    try {
      const res = await api.campaignCalls(id);
      if (!ctx.isCurrent()) return;
      const calls = res?.calls || [];
      $('#callCount').textContent = `${fmtInt(res?.total ?? calls.length)} calls`;
      if (!calls.length) { mount($('#calls'), emptyState('No calls yet', 'Calls appear here as the campaign dials each number.')); return; }
      $('#calls').style.padding = '0';
      mount($('#calls'), html`<div class="tscroll"><table class="t">
        <thead><tr><th>Lead</th><th>Outcome</th><th>Duration</th><th>Ended</th><th>Started</th></tr></thead>
        <tbody>${calls.map((c) => html`<tr class="link" data-uuid="${c.call_uuid}" tabindex="0">
          <td><b>${c.lead_name || fmtPhone(c.to_number)}</b>${c.lead_name ? html`<br><span class="faint mono" style="font-size:12px">${fmtPhone(c.to_number)}</span>` : ''}</td>
          <td><span class="chip ${outcomeTone(outcomeOf(c))}">${outcomeOf(c)}</span></td>
          <td class="mono">${fmtDuration(callDuration(c))}</td>
          <td class="faint">${c.hangup_cause ? titleCase(c.hangup_cause) : html`<span class="chip sig"><span class="live-dot"></span>Live</span>`}</td>
          <td class="faint">${fmtDateTime(c.start_time)}</td></tr>`)}</tbody></table></div>`);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      mount($('#calls'), errorState(err));
      $('#calls [data-retry]')?.addEventListener('click', loadCalls);
    }
  };
  $('#calls').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-uuid]'); if (tr) ctx.go(`/calls/${encodeURIComponent(tr.dataset.uuid)}`); });

  const loadSched = async () => {
    try {
      const tasks = (await ctx.load.schedulerTasks(true)).filter((t) => t.campaign_id === id);
      if (!ctx.isCurrent()) return;
      if (!tasks.length) { mount($('#sched'), html`<p class="faint" style="font-size:13px">No callbacks scheduled for this campaign.</p>`); return; }
      mount($('#sched'), tasks.map((t) => html`<div class="list-row">
        <span class="main-txt"><b class="mono">${fmtPhone(t.to_number)}</b><span>${fmtDateTime(t.scheduled_at)} · ${relTime(t.scheduled_at)}</span></span>
        <span class="end"><button class="btn ghost sm" data-del="${t.to_number}" aria-label="Remove callback for ${t.to_number}">${icon('x')}</button></span></div>`));
    } catch (err) {
      if (!ctx.isCurrent()) return;
      mount($('#sched'), errorState(err));
      $('#sched [data-retry]')?.addEventListener('click', loadSched);
    }
  };
  $('#sched').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-del]');
    if (!b) return;
    if (b.dataset.confirm !== '1') { b.dataset.confirm = '1'; b.textContent = 'Remove?'; b.classList.add('danger'); return; }
    b.disabled = true;
    try { await api.deleteSchedulerTask(id, b.dataset.del); toast('Callback removed', 'ok'); loadSched(); }
    catch (err) { toast(err.message, 'bad'); b.disabled = false; }
  });

  const loadEvents = async () => {
    try {
      const res = await api.campaignEvents(id);
      if (!ctx.isCurrent()) return;
      const events = (res?.events || []).slice().sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp))).slice(0, 25);
      if (!events.length) { mount($('#events'), html`<p class="faint" style="font-size:13px">No activity recorded yet.</p>`); return; }
      mount($('#events'), html`<div style="display:grid;gap:0">${events.map((ev) => {
        const d = ev.data || {};
        const detail = d.to_number ? fmtPhone(d.to_number) : d.total_numbers ? `${fmtInt(d.total_numbers)} numbers` : d.outcome || d.reason || '';
        return html`<div class="list-row" style="align-items:flex-start"><span style="width:7px;height:7px;border-radius:50%;margin-top:7px;background:var(--signal);flex:none"></span>
          <span class="main-txt"><b style="font-weight:500">${titleCase(String(ev.event_type || '').toLowerCase())}</b><span>${detail ? `${detail} · ` : ''}${relTime(ev.timestamp)}</span></span></div>`;
      })}</div>`);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      mount($('#events'), errorState(err));
      $('#events [data-retry]')?.addEventListener('click', loadEvents);
    }
  };

  await Promise.all([loadHead(), loadCalls(), loadSched(), loadEvents()]);

  const poll = setInterval(async () => {
    if (!campaign || !ACTIVE.has(campaign.status)) return;
    try {
      const progress = await api.campaignProgress(id);
      if (!ctx.isCurrent()) return;
      if (!$('#confirmArea')?.innerHTML) drawHead(campaign, progress);
      if (progress.status && progress.status !== campaign.status) { campaign.status = progress.status; loadCalls(); loadEvents(); }
    } catch { /* keep last view */ }
  }, 5000);
  const slowPoll = setInterval(() => { if (campaign && ACTIVE.has(campaign.status)) { loadCalls(); loadEvents(); } }, 20000);
  return () => { clearInterval(poll); clearInterval(slowPoll); };
}
