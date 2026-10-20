import { hbars, vbars, heatmap, callsByDay, countBy } from '../charts.js';
import {
  html, mount, fmtInt, fmtPct, fmtDuration, fmtUsd, pct, titleCase, toDate, isConnected, outcomeOf, outcomeTone, callDuration,
  callCost, loadingState, errorState, emptyState, OUTCOMES,
} from '../util.js';

const RANGES = [['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['all', 'All time']];

export async function render(el, ctx) {
  let range = '30';
  mount(el, html`
    <div class="toolbar"><div class="seg" role="group" aria-label="Date range" id="range">${RANGES.map(([v, l]) => html`<button type="button" data-r="${v}" aria-pressed="${v === range}">${l}</button>`)}</div></div>
    <div id="body">${loadingState(8)}</div>`);

  const body = el.querySelector('#body');
  let calls, summary;
  try {
    [calls, summary] = await Promise.all([ctx.load.calls(), ctx.load.summary().catch(() => null)]);
  } catch (err) {
    if (!ctx.isCurrent()) return;
    mount(body, errorState(err));
    body.querySelector('[data-retry]')?.addEventListener('click', () => { ctx.load.calls(true); ctx.rerender(); });
    return;
  }
  if (!ctx.isCurrent()) return;

  // Open on "All time" when there's been no calling in the last 30 days.
  if (calls.length && !calls.some((c) => (toDate(c.start_time || c.updated_at)?.getTime() || 0) >= Date.now() - 30 * 86400000)) {
    range = 'all';
    el.querySelectorAll('#range button').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.r === 'all')));
  }

  const draw = () => {
    const since = range === 'all' ? 0 : Date.now() - Number(range) * 86400000;
    const rows = since ? calls.filter((c) => (toDate(c.start_time || c.updated_at)?.getTime() || 0) >= since) : calls;
    if (!rows.length) {
      mount(body, emptyState(calls.length ? 'No calls in this range' : 'No calls yet', calls.length ? 'Pick a longer range to see your numbers.' : 'Analytics fill in as soon as your agent starts calling.'));
      return;
    }

    const connected = rows.filter(isConnected);
    const talked = rows.filter((c) => callDuration(c) >= 30);
    const qualified = rows.filter((c) => c.outcome === 'Qualified');
    const interested = rows.filter((c) => c.outcome === 'Qualified' || c.outcome === 'Follow-up required');
    const durations = rows.map(callDuration).filter(Boolean);
    const avgDur = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
    const costs = rows.map(callCost).filter((v) => v !== null);
    const totalCost = costs.reduce((a, b) => a + b, 0);
    const days = callsByDay(rows, range === '7' ? 7 : range === '30' ? 30 : Math.min(60, range === '90' ? 90 : 60));
    const dayFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: days.length > 14 ? undefined : 'short' });
    const fullFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const heat = heatmap(rows);

    const durByOutcome = OUTCOMES.map((o) => {
      const list = rows.filter((c) => outcomeOf(c) === o).map(callDuration);
      return { label: o, value: list.length ? list.reduce((a, b) => a + b, 0) / list.length : 0, tone: outcomeTone(o) || '' };
    }).filter((i) => i.value);

    const toBars = (pairs, tone = 'dat', label = titleCase) => pairs.slice(0, 8).map(([key, value]) => ({ label: label(key), value, tone, display: `${fmtInt(value)} · ${fmtPct(pct(value, rows.length), 0)}` }));

    mount(body, html`
      <section class="kpis">
        <div class="card kpi"><small>Calls</small><span class="v">${fmtInt(rows.length)}</span><span class="d">${fmtInt(new Set(rows.map((c) => c.to_number)).size)} unique numbers</span></div>
        <div class="card kpi"><small>Connect rate</small><span class="v">${fmtPct(pct(connected.length, rows.length))}</span><span class="d">${fmtInt(connected.length)} answered</span></div>
        <div class="card kpi accent"><small>Qualified rate</small><span class="v">${fmtPct(pct(qualified.length, rows.length))}</span><span class="d">${fmtInt(qualified.length)} qualified leads</span></div>
        <div class="card kpi"><small>Average call</small><span class="v">${fmtDuration(avgDur)}</span><span class="d">across ${fmtInt(durations.length)} calls with audio</span></div>
        <div class="card kpi"><small>Spend</small><span class="v">${costs.length ? fmtUsd(totalCost) : '—'}</span><span class="d">${!costs.length ? 'Cost appears after analysis' : qualified.length ? `${fmtUsd(totalCost / qualified.length)} per qualified lead` : `across ${fmtInt(costs.length)} analysed calls`}</span></div>
      </section>
      <div class="g" style="margin-top:16px">
        <section class="card s5"><div class="card-h"><h3>Funnel</h3><span class="faint" style="font-size:12px">share of all calls</span></div>
          ${hbars([
            { label: 'Dialed', value: rows.length, tone: 'dat' },
            { label: 'Connected', value: connected.length, tone: 'dat' },
            { label: 'Talked 30s+', value: talked.length, tone: 'sig' },
            { label: 'Interested', value: interested.length, tone: 'sig' },
            { label: 'Qualified', value: qualified.length, tone: 'ok' },
          ].map((i) => ({ ...i, display: `${fmtInt(i.value)} · ${fmtPct(pct(i.value, rows.length), 0)}` })), { max: rows.length })}
          <p class="faint" style="font-size:12px;margin-top:12px">Interested counts qualified leads plus those who asked for a follow-up.</p>
        </section>
        <section class="card s7"><div class="card-h"><h3>Calls per day</h3></div>
          ${vbars(days.map((d) => ({
            label: dayFmt.format(d.date),
            value: d.calls.length,
            tip: `${fullFmt.format(d.date)} · ${d.calls.length} calls`,
            segments: [{ value: d.calls.filter((c) => c.outcome === 'Qualified').length, color: 'var(--ok)' }, { value: Math.max(0, d.calls.filter(isConnected).length - d.calls.filter((c) => c.outcome === 'Qualified').length), color: 'var(--data)' }],
          })))}
          <div class="legend"><span><i style="background:var(--ok)"></i>Qualified</span><span><i style="background:var(--data)"></i>Connected</span><span><i style="background:var(--data-soft);border-top:2px solid var(--data)"></i>Not connected</span></div>
        </section>
        <section class="card s7"><div class="card-h"><h3>Best time to call</h3><span class="faint" style="font-size:12px">connect rate by weekday and hour, your local time</span></div>
          ${heat ? html`<div class="tscroll">${heat}</div>` : emptyState('Not enough timing data', 'Calls need a start time to appear here.')}
        </section>
        <section class="card s5"><div class="card-h"><h3>Outcomes</h3></div>
          ${hbars(toBars(countBy(rows, outcomeOf), 'dat', String).map((i) => ({ ...i, tone: outcomeTone(i.label) || '' })))}
        </section>
        <section class="card s4"><div class="card-h"><h3>Why calls ended</h3></div>
          ${(() => { const p = countBy(rows, (c) => c.hangup_cause); return p.length ? hbars(toBars(p, 'warn')) : emptyState('No hangup data', 'Shown once calls finish.'); })()}
        </section>
        <section class="card s4"><div class="card-h"><h3>Sales stage reached</h3></div>
          ${(() => { const p = countBy(rows, (c) => c.sales_stage); return p.length ? hbars(toBars(p, 'sig')) : emptyState('No stage data yet', 'Stages come from the post-call analysis.'); })()}
        </section>
        <section class="card s4"><div class="card-h"><h3>Customer tone</h3></div>
          ${(() => { const p = countBy(rows, (c) => c.emotional_tone); return p.length ? hbars(toBars(p, 'dat')) : emptyState('No tone data yet', 'Tone comes from the post-call analysis.'); })()}
        </section>
        <section class="card s6"><div class="card-h"><h3>Average length by outcome</h3></div>
          ${durByOutcome.length ? hbars(durByOutcome.map((i) => ({ ...i, display: fmtDuration(i.value) }))) : emptyState('No duration data', 'Durations appear after calls end.')}
        </section>
        <section class="card s6" style="padding:0"><div class="card-h" style="padding:16px 16px 0"><h3>Team</h3><span class="faint" style="font-size:12px">all time</span></div>
          ${summary?.member_stats?.length ? html`<div class="tscroll"><table class="t"><thead><tr><th>Member</th><th>Calls</th><th>Qualified</th><th>Follow-up</th><th>Not interested</th></tr></thead><tbody>
            ${summary.member_stats.map((m) => html`<tr><td class="mono" style="font-size:12.5px">${m.user_id}</td><td class="mono">${fmtInt(m.total_calls)}</td><td class="mono">${fmtInt(m.outcomes?.Qualified)}</td><td class="mono">${fmtInt(m.outcomes?.['Follow-up required'])}</td><td class="mono">${fmtInt(m.outcomes?.['Not interested'])}</td></tr>`)}
          </tbody></table></div>` : html`<div style="padding:16px">${emptyState('No team data', 'Member stats come from the dashboard summary.')}</div>`}
        </section>
      </div>`);
  };

  el.querySelector('#range').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-r]');
    if (!b) return;
    range = b.dataset.r;
    el.querySelectorAll('#range button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    draw();
  });
  draw();
}
