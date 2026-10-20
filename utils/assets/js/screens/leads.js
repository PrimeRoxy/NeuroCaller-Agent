import {
  html, mount, icon, fmtInt, fmtPhone, fmtDateTime, relTime, titleCase, toDate, initials,
  outcomeOf, outcomeTone, loadingState, errorState, emptyState, debounce, OUTCOMES,
} from '../util.js';

/** Collapse calls into one lead per phone number, keeping the latest signal. */
function buildLeads(calls) {
  const map = new Map();
  for (const call of calls) {
    const key = String(call.to_number || '').replace(/\D/g, '');
    if (!key) continue;
    const t = toDate(call.start_time || call.updated_at)?.getTime() || 0;
    const lead = map.get(key) || { number: call.to_number, name: '', calls: 0, last: 0, latest: null, email: '', followUp: null };
    lead.calls += 1;
    if (!lead.name && call.lead_name) lead.name = call.lead_name;
    if (!lead.email && call.extracted_email) lead.email = call.extracted_email;
    if (t >= lead.last) { lead.last = t; lead.latest = call; }
    map.set(key, lead);
  }
  for (const lead of map.values()) {
    const c = lead.latest;
    lead.outcome = outcomeOf(c);
    lead.followUp = c.follow_up_required ? (c.follow_up_date_time || c.scheduled_callback_at || 'requested') : null;
  }
  return [...map.values()].sort((a, b) => b.last - a.last);
}

export async function render(el, ctx) {
  mount(el, html`
    <div class="toolbar">
      <label class="search-input grow">${icon('search')}<span class="sr-only">Search leads</span><input class="input" id="q" type="search" placeholder="Search by name, number, or email"></label>
      <label style="display:flex;gap:8px;align-items:center;font-size:13px;color:var(--fg-2)"><input type="checkbox" id="fu"> Follow-ups only</label>
    </div>
    <div id="board">${loadingState(6)}</div>`);

  let leads;
  try {
    leads = buildLeads(await ctx.load.calls());
  } catch (err) {
    if (!ctx.isCurrent()) return;
    mount(el.querySelector('#board'), errorState(err));
    el.querySelector('[data-retry]')?.addEventListener('click', () => { ctx.load.calls(true); ctx.rerender(); });
    return;
  }
  if (!ctx.isCurrent()) return;

  const state = { q: '', fu: false };
  const board = el.querySelector('#board');

  const draw = () => {
    const q = state.q.toLowerCase();
    const rows = leads.filter((l) => (!state.fu || l.followUp) && (!q || `${l.name} ${l.number} ${l.email}`.toLowerCase().includes(q)));
    if (!leads.length) {
      mount(board, emptyState('No leads yet', 'Everyone your agent calls becomes a lead here, sorted by the outcome of their latest call.', html`<a class="btn sm primary" href="#/copilot">Start calls with Copilot</a>`));
      return;
    }
    const cols = [...OUTCOMES, ...new Set(rows.map((l) => l.outcome).filter((o) => !OUTCOMES.includes(o)))];
    mount(board, html`<div class="kanban" style="grid-template-columns:repeat(${cols.length},minmax(230px,1fr))">${cols.map((col) => {
      const items = rows.filter((l) => l.outcome === col);
      return html`<section class="kcol" aria-label="${col}">
        <h4><span style="display:flex;gap:8px;align-items:center"><span class="chip ${outcomeTone(col)}" style="padding:3px 6px">${fmtInt(items.length)}</span>${col}</span></h4>
        ${items.slice(0, 60).map((l) => html`
          <a class="kcard" href="#/calls/${encodeURIComponent(l.latest.call_uuid)}">
            <span style="display:flex;gap:10px;align-items:center;min-width:0"><span class="av" style="width:28px;height:28px;font-size:11px">${initials(l.name, l.number)}</span>
              <span style="min-width:0"><b style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${l.name || fmtPhone(l.number)}</b>${l.name ? html`<span class="faint mono" style="font-size:11.5px">${fmtPhone(l.number)}</span>` : ''}</span></span>
            ${l.latest.next_recommended_action ? html`<span class="muted" style="font-size:12.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${l.latest.next_recommended_action}</span>` : ''}
            <span class="meta">
              <span>${fmtInt(l.calls)} call${l.calls === 1 ? '' : 's'} · ${relTime(l.last)}</span>
              ${l.latest.sales_stage ? html`<span class="chip">${titleCase(l.latest.sales_stage)}</span>` : ''}
              ${l.followUp ? html`<span class="chip sig">${icon('calendar', 'style="width:11px;height:11px"')}${l.followUp === 'requested' ? 'Follow-up' : fmtDateTime(l.followUp)}</span>` : ''}
            </span>
          </a>`)}
        ${items.length > 60 ? html`<p class="faint" style="font-size:12px">${fmtInt(items.length - 60)} more. Search to narrow down.</p>` : ''}
        ${!items.length ? html`<p class="faint" style="font-size:12.5px">None</p>` : ''}
      </section>`;
    })}</div>
    <p class="faint" style="font-size:12.5px">${fmtInt(rows.length)} of ${fmtInt(leads.length)} leads. Each lead is placed by the outcome of their most recent call.</p>`);
  };

  el.querySelector('#q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); draw(); }, 150));
  el.querySelector('#fu').addEventListener('change', (e) => { state.fu = e.target.checked; draw(); });
  draw();
}
