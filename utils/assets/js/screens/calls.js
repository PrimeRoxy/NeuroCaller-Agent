import {
  html, mount, icon, fmtInt, fmtPhone, fmtDateTime, fmtDuration, fmtUsd, titleCase, shortId, campaignLabel, toDate,
  outcomeOf, outcomeTone, isConnected, callCost, callDuration, parseTranscript, loadingState, errorState, emptyState,
  debounce, downloadCsv, copyText, OUTCOMES,
} from '../util.js';

const PAGE = 50;
const RANGES = [['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['all', 'All time']];

export async function render(el, ctx) {
  if (ctx.params[0]) return renderDetail(el, ctx, ctx.params[0]);

  const state = { q: '', outcome: '', campaign: '', range: '30', page: 0 };
  ctx.setActions(html`<button class="btn sm" id="exportCsv">${icon('download')}<span class="hide-sm">Export CSV</span></button>`);

  mount(el, html`
    <div class="toolbar">
      <label class="search-input grow">${icon('search')}<span class="sr-only">Search calls</span><input class="input" id="q" type="search" placeholder="Search by name, number, or call ID"></label>
      <select class="input" id="outcome" style="width:auto" aria-label="Filter by outcome"><option value="">All outcomes</option>${OUTCOMES.map((o) => html`<option>${o}</option>`)}</select>
      <select class="input" id="campaign" style="width:auto;max-width:200px" aria-label="Filter by campaign"><option value="">All campaigns</option></select>
      <div class="seg" role="group" aria-label="Date range" id="range">${RANGES.map(([v, l]) => html`<button type="button" data-r="${v}" aria-pressed="${v === state.range}">${l}</button>`)}</div>
    </div>
    <section class="card" style="padding:0"><div id="table" style="padding:16px">${loadingState(8)}</div></section>`);

  let calls;
  try {
    calls = await ctx.load.calls();
  } catch (err) {
    if (!ctx.isCurrent()) return undefined;
    mount(el.querySelector('#table'), errorState(err));
    el.querySelector('[data-retry]')?.addEventListener('click', () => { ctx.load.calls(true); ctx.rerender(); });
    return undefined;
  }
  if (!ctx.isCurrent()) return undefined;

  const recent = Date.now() - 30 * 86400000;
  if (calls.length && !calls.some((c) => (toDate(c.start_time || c.updated_at)?.getTime() || 0) >= recent)) {
    state.range = 'all';
    el.querySelectorAll('#range button').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.r === 'all')));
  }

  const campSel = el.querySelector('#campaign');
  for (const id of [...new Set(calls.map((c) => c.campaign_id).filter(Boolean))]) {
    const opt = document.createElement('option');
    opt.value = id; opt.textContent = campaignLabel(id);
    campSel.appendChild(opt);
  }

  const filtered = () => {
    const q = state.q.toLowerCase();
    const since = state.range === 'all' ? 0 : Date.now() - Number(state.range) * 86400000;
    return calls.filter((c) => {
      if (state.outcome && outcomeOf(c) !== state.outcome) return false;
      if (state.campaign && c.campaign_id !== state.campaign) return false;
      if (since) { const t = toDate(c.start_time || c.updated_at)?.getTime(); if (!t || t < since) return false; }
      if (q) {
        const hay = `${c.lead_name || ''} ${c.to_number || ''} ${c.from_number || ''} ${c.call_uuid || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  };

  const table = el.querySelector('#table');
  const draw = () => {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.page = Math.min(state.page, pages - 1);
    const slice = rows.slice(state.page * PAGE, state.page * PAGE + PAGE);
    if (!rows.length) {
      table.style.padding = '16px';
      mount(table, calls.length
        ? emptyState('No calls match', 'Try a wider date range or clear the filters.')
        : emptyState('No calls yet', 'Every call your agent makes lands here with its recording, transcript, and outcome.', html`<a class="btn sm primary" href="#/copilot">Start calls with Copilot</a>`));
      return;
    }
    table.style.padding = '0';
    mount(table, html`<div class="tscroll"><table class="t">
      <thead><tr><th>Lead</th><th>Outcome</th><th>Stage</th><th>Duration</th><th>Ended</th><th>Started</th><th>Cost</th></tr></thead>
      <tbody>${slice.map((c) => html`<tr class="link" data-uuid="${c.call_uuid}" tabindex="0">
        <td><b>${c.lead_name || fmtPhone(c.to_number)}</b>${c.lead_name ? html`<br><span class="faint mono" style="font-size:12px">${fmtPhone(c.to_number)}</span>` : ''}</td>
        <td><span class="chip ${outcomeTone(outcomeOf(c))}">${outcomeOf(c)}</span>${c.follow_up_required ? html` <span class="chip sig" title="Follow-up requested">${icon('calendar', 'style="width:11px;height:11px"')}</span>` : ''}</td>
        <td class="muted">${c.sales_stage ? titleCase(c.sales_stage) : '—'}</td>
        <td class="mono">${fmtDuration(callDuration(c))}</td>
        <td class="faint">${c.hangup_cause ? titleCase(c.hangup_cause) : html`<span class="chip sig"><span class="live-dot"></span>Live</span>`}</td>
        <td class="faint" style="white-space:nowrap">${fmtDateTime(c.start_time)}</td>
        <td class="mono faint">${fmtUsd(callCost(c))}</td></tr>`)}</tbody></table></div>
      <div class="pager" style="padding:12px 16px"><span>${fmtInt(rows.length)} calls${rows.length > PAGE ? ` · page ${state.page + 1} of ${pages}` : ''}</span>
        ${rows.length > PAGE ? html`<span style="display:flex;gap:6px"><button class="btn sm" data-page="-1" ${state.page === 0 ? 'disabled' : ''}>Previous</button><button class="btn sm" data-page="1" ${state.page >= pages - 1 ? 'disabled' : ''}>Next</button></span>` : ''}</div>`);
  };

  const reset = () => { state.page = 0; draw(); };
  el.querySelector('#q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); reset(); }, 150));
  el.querySelector('#outcome').addEventListener('change', (e) => { state.outcome = e.target.value; reset(); });
  campSel.addEventListener('change', (e) => { state.campaign = e.target.value; reset(); });
  el.querySelector('#range').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-r]');
    if (!b) return;
    state.range = b.dataset.r;
    el.querySelectorAll('#range button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    reset();
  });
  table.addEventListener('click', (e) => {
    const pg = e.target.closest('[data-page]');
    if (pg) { state.page += Number(pg.dataset.page); draw(); return; }
    const tr = e.target.closest('tr[data-uuid]');
    if (tr) ctx.go(`/calls/${encodeURIComponent(tr.dataset.uuid)}`);
  });
  table.addEventListener('keydown', (e) => { const tr = e.target.closest('tr[data-uuid]'); if (tr && e.key === 'Enter') ctx.go(`/calls/${encodeURIComponent(tr.dataset.uuid)}`); });

  document.getElementById('exportCsv').addEventListener('click', () => {
    const rows = filtered();
    downloadCsv(`neurocaller-calls-${new Date().toISOString().slice(0, 10)}.csv`, [
      ['call_uuid', 'lead_name', 'to_number', 'from_number', 'outcome', 'sales_stage', 'duration_seconds', 'hangup_cause', 'follow_up_required', 'follow_up_date_time', 'start_time', 'campaign_id', 'cost_usd'],
      ...rows.map((c) => [c.call_uuid, c.lead_name, c.to_number, c.from_number, outcomeOf(c), c.sales_stage, Math.round(callDuration(c)), c.hangup_cause, c.follow_up_required, c.follow_up_date_time, c.start_time, c.campaign_id, callCost(c)]),
    ]);
  });

  draw();
  return undefined;
}

async function renderDetail(el, ctx, uuid) {
  ctx.setTitle('Call detail', shortId(uuid));
  mount(el, html`<a class="back-link" href="#/calls">${icon('back')}All calls</a><div id="body">${loadingState(6)}</div>`);
  const body = el.querySelector('#body');

  let call;
  try {
    const calls = await ctx.load.calls();
    call = calls.find((c) => c.call_uuid === uuid);
    if (!call) call = (await ctx.load.calls(true)).find((c) => c.call_uuid === uuid);
  } catch (err) {
    if (!ctx.isCurrent()) return;
    mount(body, errorState(err));
    body.querySelector('[data-retry]')?.addEventListener('click', () => { ctx.load.calls(true); ctx.rerender(); });
    return;
  }
  if (!ctx.isCurrent()) return;
  if (!call) {
    mount(body, emptyState('Call not found', "This call isn't in this workspace yet. Live calls show up here once the call record is saved.", html`<a class="btn sm" href="#/calls">Back to calls</a>`));
    return;
  }

  ctx.setTitle(call.lead_name || fmtPhone(call.to_number), `${fmtDateTime(call.start_time)} · ${fmtDuration(callDuration(call))}`);
  const outcome = outcomeOf(call);
  const turns = parseTranscript(call.transcript || call.conversation_history);
  const cost = callCost(call);
  const followUp = call.follow_up_date_time || call.scheduled_callback_at;

  mount(body, html`
    <section class="card" style="display:grid;gap:14px">
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <span class="chip ${outcomeTone(outcome)}">${outcome}</span>
        ${call.sales_stage ? html`<span class="chip dat">${titleCase(call.sales_stage)}</span>` : ''}
        ${call.follow_up_required ? html`<span class="chip sig">${icon('calendar', 'style="width:11px;height:11px"')}Follow-up${followUp ? ` · ${fmtDateTime(followUp)}` : ''}</span>` : ''}
        ${call.hangup_cause ? html`<span class="chip ${isConnected(call) ? 'ok' : 'warn'}">${titleCase(call.hangup_cause)}</span>` : html`<span class="chip sig"><span class="live-dot"></span>Live</span>`}
        <span style="margin-left:auto"><button class="btn ghost sm mono" data-copy="${call.call_uuid}">${shortId(call.call_uuid)} ${icon('copy')}</button></span>
      </div>
      ${call.recording_url
        ? html`<audio controls preload="none" src="${call.recording_url}"></audio>`
        : html`<p class="faint" style="font-size:13px">No recording saved for this call.</p>`}
    </section>
    <div class="g">
      <section class="card s7"><div class="card-h"><h3>Transcript</h3><span class="faint" style="font-size:12px">${turns.length ? `${fmtInt(turns.length)} turns` : ''}</span></div>
        ${turns.length ? html`<div class="transcript">${turns.map((t) => html`<div class="turn ${t.speaker}"><small>${t.label}</small><p>${t.text}</p></div>`)}</div>`
          : emptyState('No transcript yet', call.hangup_cause ? 'The transcript appears once the recording has been processed.' : 'The transcript is created after the call ends.')}
      </section>
      <div class="s5" style="display:grid;gap:16px;align-content:start">
        <section class="card"><div class="card-h"><h3>AI analysis</h3></div>
          ${call.explanation || call.outcome_reason || call.lead_reason ? html`<p style="font-size:14px;margin-bottom:12px">${call.explanation || call.outcome_reason || call.lead_reason}</p>` : ''}
          ${call.next_recommended_action ? html`<div class="confirm" style="margin-bottom:12px"><span><b>Next step</b><br>${call.next_recommended_action}</span></div>` : ''}
          <dl class="kv">
            ${row('Outcome', outcome)}
            ${row('Sales stage', call.sales_stage && titleCase(call.sales_stage))}
            ${row('Lead status', call.lead_status && titleCase(call.lead_status))}
            ${row('Tone', call.emotional_tone && titleCase(call.emotional_tone))}
            ${row('Confidence', call.confidence !== undefined && call.confidence !== null ? String(call.confidence) : null)}
            ${row('Background noise', call.background_noise && titleCase(String(call.background_noise)))}
            ${row('Follow-up', followUp ? fmtDateTime(followUp) : call.follow_up_required ? 'Requested, no time set' : null)}
            ${row('Email captured', call.extracted_email)}
          </dl>
          ${!call.explanation && !call.sales_stage && !call.next_recommended_action ? html`<p class="faint" style="font-size:13px;margin-top:8px">Analysis runs after the recording is processed.</p>` : ''}
        </section>
        <section class="card"><div class="card-h"><h3>Call details</h3></div>
          <dl class="kv">
            ${row('Lead', call.lead_name)}
            ${row('Called', fmtPhone(call.to_number))}
            ${row('Caller ID', call.from_number && fmtPhone(call.from_number))}
            ${row('Started', fmtDateTime(call.start_time))}
            ${row('Ended', call.end_time && fmtDateTime(call.end_time))}
            ${row('Duration', fmtDuration(callDuration(call)))}
            ${row('Voice', call.agent_voice && titleCase(call.agent_voice))}
            ${row('Cost', cost !== null ? fmtUsd(cost) : null)}
            ${call.campaign_id ? html`<dt>Campaign</dt><dd><a href="#/campaigns/${encodeURIComponent(call.campaign_id)}" >${campaignLabel(call.campaign_id)}</a></dd>` : ''}
          </dl>
        </section>
      </div>
    </div>`);

  body.addEventListener('click', (e) => { const c = e.target.closest('[data-copy]'); if (c) copyText(c.dataset.copy); });
}

function row(label, value) {
  if (value === null || value === undefined || value === '' || value === false) return '';
  return html`<dt>${label}</dt><dd>${value}</dd>`;
}
