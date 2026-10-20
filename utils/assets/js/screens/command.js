import { onLive, liveCalls } from '../state.js';
import { sparkline, hbars, vbars, callsByDay, countBy } from '../charts.js';
import {
  html, mount, icon, fmtInt, fmtPct, fmtMinutes, fmtPhone, relTime, toDate, pct, initials, callDuration,
  isConnected, outcomeOf, outcomeTone, campaignTone, campaignLabel, titleCase, fmtDateTime,
  loadingState, errorState, emptyState, OUTCOMES,
} from '../util.js';
import { toneColor } from '../charts.js';

export async function render(el, ctx) {
  ctx.setActions(html`<button class="btn sm" id="refresh">${icon('refresh')}<span class="hide-sm">Refresh</span></button>`);
  document.getElementById('refresh').addEventListener('click', () => {
    ctx.load.calls(true); ctx.load.summary(true); ctx.load.campaigns(true);
    ctx.rerender();
  });

  mount(el, html`
    <section class="kpis" id="kpis">${Array.from({ length: 5 }, () => html`<div class="card kpi">${loadingState(2)}</div>`)}</section>
    <div class="g">
      <section class="card s5" id="liveCard"><div class="card-h"><h3>Live right now</h3><a class="btn ghost sm" href="#/monitor">Open monitor</a></div><div id="liveBody"></div></section>
      <section class="card s7" id="insights"><div class="card-h"><h3>Insights</h3><span class="chip">From your call data</span></div><div id="insBody">${loadingState(3)}</div></section>
      <section class="card s6"><div class="card-h"><h3>Call outcomes</h3><a class="btn ghost sm" href="#/leads">View leads</a></div><div id="outcomes">${loadingState(4)}</div></section>
      <section class="card s6"><div class="card-h"><h3>Campaigns</h3><a class="btn ghost sm" href="#/campaigns">All campaigns</a></div><div id="camps">${loadingState(4)}</div></section>
      <section class="card s8"><div class="card-h"><h3>Calls per day</h3><span class="faint mono" style="font-size:12px">last 14 days</span></div><div id="perDay">${loadingState(4)}</div></section>
      <section class="card s4"><div class="card-h"><h3>Team</h3><span class="faint" style="font-size:12px">calls by member</span></div><div id="team">${loadingState(3)}</div></section>
    </div>`);

  const $ = (id) => el.querySelector(`#${id}`);
  const fail = (id, err) => {
    if (!ctx.isCurrent()) return;
    mount($(id), errorState(err));
    $(id).querySelector('[data-retry]')?.addEventListener('click', () => ctx.rerender());
  };

  // Live feed
  const unsub = onLive((live) => {
    if (!ctx.isCurrent()) return;
    const calls = liveCalls().slice(0, 5);
    if (!calls.length) {
      mount($('liveBody'), emptyState(
        live.status === 'live' ? 'No calls in progress' : 'Connecting to the live feed',
        live.status === 'live' ? 'Calls appear here the moment your agent dials.' : 'Live updates start as soon as the connection opens.',
        html`<a class="btn sm" href="#/copilot">Start calls with Copilot</a>`));
      return;
    }
    mount($('liveBody'), calls.map((c) => html`
      <a class="list-row" href="#/calls/${encodeURIComponent(c.call_uuid)}">
        <span class="av ${c.hangup_cause ? '' : 'sig'}">${initials('', c.to_number)}</span>
        <span class="main-txt"><b>${fmtPhone(c.to_number)}</b><span>${c.lead_reason || (c.hangup_cause ? titleCase(c.hangup_cause) : 'In progress')} · ${relTime(c.updated_at)}</span></span>
        <span class="end">${c.hangup_cause ? html`<span class="chip">Ended</span>` : html`<span class="chip sig"><span class="live-dot"></span>Live</span>`}${c.lead_status ? html`<span class="chip dat">${titleCase(c.lead_status)}</span>` : ''}</span>
      </a>`));
  });

  const [summaryRes, callsRes, campsRes] = await Promise.allSettled([ctx.load.summary(), ctx.load.calls(), ctx.load.campaigns()]);
  if (!ctx.isCurrent()) return unsub;

  const summary = summaryRes.status === 'fulfilled' ? summaryRes.value : null;
  const calls = callsRes.status === 'fulfilled' ? callsRes.value : null;

  // KPIs
  if (!summary && !calls) {
    fail('kpis', summaryRes.reason || callsRes.reason);
  } else {
    const days = calls ? callsByDay(calls, 14) : [];
    const todayCount = days.length ? days[days.length - 1].calls.length : 0;
    const qualified = summary?.outcome_counts?.Qualified ?? (calls ? calls.filter((c) => c.outcome === 'Qualified').length : 0);
    const totalCalls = calls ? calls.length : summary.member_stats.reduce((s, m) => s + m.total_calls, 0);
    // Computed from call records when available, so VoiceLink's ANSWER status counts as connected.
    const connectRate = calls ? pct(calls.filter(isConnected).length, calls.length) : summary.call_success_rate;
    const talkSeconds = calls ? calls.reduce((s, c) => s + callDuration(c), 0) : null;
    const talkMinutes = talkSeconds !== null ? talkSeconds / 60 : summary?.total_duration_minutes;
    const timedCalls = calls ? calls.filter((c) => callDuration(c) > 0).length : 0;
    const avgMinutes = calls ? (timedCalls ? talkMinutes / timedCalls : 0) : summary?.avg_duration_minutes;
    mount($('kpis'), html`
      <div class="card kpi"><small>Calls made</small><span class="v">${fmtInt(totalCalls)}</span><span class="d">${fmtInt(todayCount)} today</span>${sparkline(days.map((d) => d.calls.length))}</div>
      <div class="card kpi"><small>Connect rate</small><span class="v">${fmtPct(connectRate)}</span><span class="d">calls that were answered</span>${sparkline(days.map((d) => pct(d.calls.filter(isConnected).length, d.calls.length)), 'var(--signal)')}</div>
      <div class="card kpi accent"><small>Qualified leads</small><span class="v">${fmtInt(qualified)}</span><span class="d">${fmtPct(pct(qualified, totalCalls))} of calls</span>${sparkline(days.map((d) => d.calls.filter((c) => c.outcome === 'Qualified').length), 'var(--ok)')}</div>
      <div class="card kpi"><small>Talk time</small><span class="v">${talkMinutes !== undefined ? fmtMinutes(talkMinutes) : '—'}</span><span class="d">${avgMinutes !== undefined ? `${fmtMinutes(avgMinutes)} average call` : 'Summary unavailable'}</span></div>
      <div class="card kpi"><small>Follow-ups needed</small><span class="v">${fmtInt(summary?.follow_up_required_count ?? calls.filter((c) => c.follow_up_required).length)}</span><span class="d">${summary ? `${fmtInt(summary.active_campaigns)} active of ${fmtInt(summary.total_campaigns)} campaigns` : ''}</span></div>`);
  }

  // Outcomes
  if (summary || calls) {
    const counts = summary?.outcome_counts || Object.fromEntries(countBy(calls, outcomeOf));
    const keys = [...OUTCOMES, ...Object.keys(counts).filter((k) => !OUTCOMES.includes(k))];
    const items = keys.filter((k) => counts[k]).map((k) => ({ label: k, value: counts[k], tone: outcomeTone(k) || '' }));
    const total = items.reduce((s, i) => s + i.value, 0);
    mount($('outcomes'), items.length
      ? hbars(items.map((i) => ({ ...i, display: `${fmtInt(i.value)} · ${fmtPct(pct(i.value, total), 0)}` })))
      : emptyState('No outcomes yet', 'Each finished call is analysed and sorted into an outcome.'));
  } else fail('outcomes', summaryRes.reason);

  // Campaigns
  if (campsRes.status === 'fulfilled') {
    const camps = campsRes.value.slice(0, 5);
    mount($('camps'), camps.length ? camps.map((c) => {
      const total = c.total_numbers || c.total || 0;
      const done = (c.completed || 0) + (c.failed || 0);
      return html`<a class="list-row" href="#/campaigns/${encodeURIComponent(c.campaign_id)}" style="display:grid;gap:8px">
        <span style="display:flex;justify-content:space-between;gap:8px;align-items:center;min-width:0">
          <span class="main-txt"><b>${campaignLabel(c.campaign_id)}</b><span>Started ${relTime(c.started_at || c.created_at)}</span></span>
          <span class="end"><span class="mono faint" style="font-size:12px">${fmtInt(done)} / ${fmtInt(total)}</span><span class="chip ${campaignTone(c.status)}">${titleCase(c.status)}</span></span>
        </span>
        <span class="prog"><i class="ok" style="width:${pct(c.completed || 0, total)}%"></i><i class="bad" style="width:${pct(c.failed || 0, total)}%"></i><i class="live" style="width:${pct(c.in_progress || 0, total)}%"></i></span>
      </a>`;
    }) : emptyState('No campaigns yet', 'Ask Copilot to call a list of numbers and your first campaign appears here.', html`<a class="btn sm primary" href="#/copilot">Open Copilot</a>`));
  } else fail('camps', campsRes.reason);

  // Calls per day
  if (calls) {
    const days = callsByDay(calls, 14);
    const fmtDay = new Intl.DateTimeFormat(undefined, { day: 'numeric' });
    const fmtFull = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const inWindow = days.some((d) => d.calls.length);
    mount($('perDay'), inWindow ? html`
      ${vbars(days.map((d) => {
        const q = d.calls.filter((c) => c.outcome === 'Qualified').length;
        const conn = d.calls.filter(isConnected).length - q;
        return {
          label: fmtDay.format(d.date),
          value: d.calls.length,
          tip: `${fmtFull.format(d.date)} · ${d.calls.length} calls, ${q} qualified`,
          segments: [{ value: q, color: 'var(--ok)' }, { value: Math.max(0, conn), color: 'var(--data)' }],
        };
      }))}
      <div class="legend"><span><i style="background:var(--ok)"></i>Qualified</span><span><i style="background:var(--data)"></i>Connected</span><span><i style="background:var(--data-soft);border-top:2px solid var(--data)"></i>Not connected</span></div>`
      : calls.length
        ? emptyState('No calls in the last 14 days', `Your most recent call was ${fmtDateTime(calls[0].start_time || calls[0].updated_at)}. See every day in Analytics.`, html`<a class="btn sm" href="#/analytics">Open analytics</a>`)
        : emptyState('No calls yet', 'Daily call volume shows up once your agent starts dialing.'));
  } else fail('perDay', callsRes.reason);

  // Team
  if (summary) {
    const members = summary.member_stats.slice(0, 6);
    mount($('team'), members.length ? members.map((m) => html`
      <div class="list-row"><span class="av">${initials(m.user_id)}</span>
        <span class="main-txt"><b class="mono" style="font-size:12.5px">${m.user_id}</b><span>${fmtInt(m.outcomes?.Qualified || 0)} qualified</span></span>
        <span class="end mono">${fmtInt(m.total_calls)}</span></div>`)
      : emptyState('No team activity', 'Member stats appear after the first calls.'));
  } else fail('team', summaryRes.reason);

  // Insights
  if (calls) mount($('insBody'), insights(calls));
  else fail('insBody', callsRes.reason);

  return unsub;
}

function insights(calls) {
  const out = [];
  const now = Date.now();

  const due = calls.filter((c) => c.follow_up_required);
  if (due.length) {
    const upcoming = due.map((c) => toDate(c.follow_up_date_time || c.scheduled_callback_at)).filter((d) => d && d.getTime() > now).sort((a, b) => a - b);
    out.push({
      tone: 'sig',
      text: `${fmtInt(due.length)} lead${due.length === 1 ? '' : 's'} asked for a follow-up.${upcoming.length ? ` Next one is ${fmtDateTime(upcoming[0])}.` : ''}`,
      href: '#/leads', cta: 'See follow-ups',
    });
  }

  const byHour = new Map();
  for (const c of calls) {
    const d = toDate(c.start_time);
    if (!d) continue;
    const h = d.getHours();
    const e = byHour.get(h) || { total: 0, ok: 0 };
    e.total += 1; if (isConnected(c)) e.ok += 1;
    byHour.set(h, e);
  }
  const ranked = [...byHour.entries()].filter(([, e]) => e.total >= 5).map(([h, e]) => ({ h, rate: e.ok / e.total, total: e.total })).sort((a, b) => b.rate - a.rate);
  if (ranked.length >= 2) {
    const best = ranked[0], worst = ranked[ranked.length - 1];
    out.push({
      tone: 'dat',
      text: `Calls around ${best.h}:00 connect ${Math.round(best.rate * 100)}% of the time, versus ${Math.round(worst.rate * 100)}% around ${worst.h}:00.`,
      href: '#/analytics', cta: 'Open analytics',
    });
  }

  const endings = countBy(calls.filter((c) => c.hangup_cause && !isConnected(c)), (c) => c.hangup_cause);
  if (endings.length) {
    const [cause, n] = endings[0];
    out.push({ tone: 'warn', text: `${fmtInt(n)} call${n === 1 ? '' : 's'} didn't connect. The most common reason is "${titleCase(cause)}".`, href: '#/calls', cta: 'Review calls' });
  }

  const week = 7 * 86400000;
  const inRange = (c, from, to) => { const t = toDate(c.start_time)?.getTime(); return t && t >= from && t < to; };
  const thisWeek = calls.filter((c) => inRange(c, now - week, now + 1));
  const lastWeek = calls.filter((c) => inRange(c, now - 2 * week, now - week));
  if (thisWeek.length && lastWeek.length) {
    const a = pct(thisWeek.filter((c) => c.outcome === 'Qualified').length, thisWeek.length);
    const b = pct(lastWeek.filter((c) => c.outcome === 'Qualified').length, lastWeek.length);
    const diff = a - b;
    out.push({ tone: diff >= 0 ? 'ok' : 'bad', text: `Qualified rate is ${fmtPct(a)} this week, ${diff >= 0 ? 'up' : 'down'} ${fmtPct(Math.abs(diff))} on last week.`, href: '#/analytics', cta: 'Compare' });
  }

  if (!out.length) return emptyState('Not enough data yet', 'Insights appear once there are a few days of calls to compare.');
  return html`<div style="display:grid;gap:10px">${out.slice(0, 4).map((i) => html`
    <div style="display:flex;gap:12px;align-items:flex-start;padding:12px;border-radius:10px;background:var(--bg-3)">
      <span style="width:8px;height:8px;border-radius:50%;margin-top:7px;flex:none;background:${toneColor(i.tone)};box-shadow:0 0 10px ${toneColor(i.tone)}"></span>
      <p style="flex:1;font-size:13.5px">${i.text}</p>
      <a class="btn sm" href="${i.href}">${i.cta}</a>
    </div>`)}</div>`;
}
