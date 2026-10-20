import { api } from '../api.js';
import { onLive, liveCalls } from '../state.js';
import { html, mount, icon, fmtPhone, relTime, titleCase, initials, emptyState, toast } from '../util.js';

export async function render(el, ctx) {
  mount(el, html`
    <div class="g">
      <section class="s8" style="display:grid;gap:12px;align-content:start">
        <div class="toolbar"><h3 style="font-size:16px">Calls</h3><span class="chip" id="feedState">Connecting</span><span class="faint" style="font-size:12.5px">Updates stream in as each call changes</span></div>
        <div id="grid"></div>
      </section>
      <section class="card s4" style="align-self:start">
        <div class="card-h"><h3>Call a number now</h3>${icon('phone', 'style="width:18px;height:18px;color:var(--signal)"')}</div>
        <form id="qc" class="form-grid" style="grid-template-columns:minmax(0,1fr)" novalidate>
          <div class="field"><label for="qcPhone">Phone number</label><input class="input mono" id="qcPhone" inputmode="tel" autocomplete="tel" placeholder="+91 98765 43210" required><span class="err" id="qcErr" role="alert"></span></div>
          <div class="field"><label for="qcName">Lead name <span class="faint">(optional)</span></label><input class="input" id="qcName" autocomplete="off" placeholder="Priya Sharma"></div>
          <div class="field"><label for="qcVoice">Voice</label><select class="input" id="qcVoice"><option value="">Server default</option></select></div>
          <div class="field"><label for="qcWelcome">Opening line <span class="faint">(optional)</span></label><textarea class="input" id="qcWelcome" rows="3" style="min-height:84px" placeholder="Leave empty to use the server's default greeting"></textarea></div>
          <div id="qcConfirm"></div>
          <button class="btn primary" type="submit" id="qcBtn">${icon('phone')}Review call</button>
          <p class="faint" style="font-size:12px">This places a real phone call. Quick calls are logged under the quick-call workspace set on the server.</p>
        </form>
      </section>
    </div>`);

  const grid = el.querySelector('#grid');
  const seen = new Map();

  let lastLive = null;
  const draw = (live) => {
    lastLive = live;
    if (!ctx.isCurrent()) return;
    const state = el.querySelector('#feedState');
    state.className = `chip ${live.status === 'live' ? 'ok' : 'warn'}`;
    state.textContent = { live: 'Live feed on', connecting: 'Connecting', reconnecting: 'Reconnecting', idle: 'Offline' }[live.status] || live.status;

    const calls = liveCalls().sort((a, b) => Number(!!a.hangup_cause) - Number(!!b.hangup_cause));
    if (!calls.length) {
      mount(grid, emptyState('No calls in progress', 'When your agent dials, each call appears here with its live status and lead signal.', html`<a class="btn sm" href="#/copilot">Start calls with Copilot</a>`));
      return;
    }
    mount(grid, html`<div class="live-grid">${calls.map((c) => {
      const on = !c.hangup_cause;
      const changed = c._changed && seen.get(c.call_uuid) !== c._changed;
      seen.set(c.call_uuid, c._changed);
      return html`
        <article class="live-card ${on ? 'on' : ''} ${changed ? 'flash' : ''}">
          <div class="hd"><span class="av ${on ? 'sig' : ''}">${initials('', c.to_number)}</span>
            <span class="who"><b class="mono">${fmtPhone(c.to_number)}</b><span>from ${fmtPhone(c.from_number)}</span></span>
            ${on ? html`<span class="chip sig"><span class="live-dot"></span>Live</span>` : html`<span class="chip">${titleCase(c.hangup_cause)}</span>`}
          </div>
          <div class="eq" aria-hidden="true">${Array.from({ length: 28 }, (_, i) => html`<i style="animation-delay:${((i * 137) % 1000) / 1000}s"></i>`)}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${c.lead_status ? html`<span class="chip dat">${titleCase(c.lead_status)}</span>` : html`<span class="chip">Lead signal pending</span>`}
            ${c.live_analysis_enabled ? html`<span class="chip ok">Live analysis on</span>` : ''}
          </div>
          ${c.lead_reason ? html`<p style="font-size:13px" class="muted">${c.lead_reason}</p>` : ''}
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <span class="faint" style="font-size:12px">Updated ${relTime(c.updated_at)}</span>
            <a class="btn sm" href="#/calls/${encodeURIComponent(c.call_uuid)}">Open call</a>
          </div>
        </article>`;
    })}</div>`);
  };
  const unsub = onLive(draw);

  // Keep relative times fresh.
  const tick = setInterval(() => lastLive && draw(lastLive), 30000);

  // Voices for the quick call (India catalogue carries the Gemini voice IDs).
  ctx.load.voices('IN').then((voices) => {
    if (!ctx.isCurrent()) return;
    const select = el.querySelector('#qcVoice');
    for (const v of voices.filter((x) => x.voice_id)) {
      const opt = document.createElement('option');
      opt.value = v.voice_id;
      opt.textContent = `${titleCase(v.name)} · ${v.gender}`;
      select.appendChild(opt);
    }
  }).catch(() => { /* keep server default */ });

  const form = el.querySelector('#qc');
  const err = el.querySelector('#qcErr');
  const confirmBox = el.querySelector('#qcConfirm');
  const btn = el.querySelector('#qcBtn');
  form.addEventListener('input', () => { err.textContent = ''; confirmBox.innerHTML = ''; btn.hidden = false; });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const phone = el.querySelector('#qcPhone').value.trim();
    const digits = phone.replace(/\D/g, '');
    if (digits.length < 10 || digits.length > 15) {
      err.textContent = 'Enter a phone number with 10 to 15 digits, including the country code if outside India.';
      el.querySelector('#qcPhone').focus();
      return;
    }
    const shown = digits.length === 10 ? `+91 ${digits}` : `+${digits}`;
    btn.hidden = true;
    mount(confirmBox, html`<div class="confirm"><span style="flex:1">Call <b class="mono">${shown}</b> now?</span><button class="btn sm primary" type="button" id="qcGo">Call now</button><button class="btn sm" type="button" id="qcCancel">Cancel</button></div>`);
    confirmBox.querySelector('#qcCancel').addEventListener('click', () => { confirmBox.innerHTML = ''; btn.hidden = false; });
    confirmBox.querySelector('#qcGo').addEventListener('click', async (ev) => {
      ev.currentTarget.disabled = true;
      ev.currentTarget.textContent = 'Dialing…';
      try {
        const res = await api.quickCall({
          phone_number: phone,
          lead_name: el.querySelector('#qcName').value.trim(),
          voice: el.querySelector('#qcVoice').value || null,
          welcome_message: el.querySelector('#qcWelcome').value.trim() || null,
        });
        toast(res?.message || `Calling ${shown}`, 'ok');
        form.reset();
      } catch (e2) {
        toast(e2.message, 'bad');
      } finally {
        if (ctx.isCurrent()) { confirmBox.innerHTML = ''; btn.hidden = false; }
      }
    });
  });

  return () => { unsub(); clearInterval(tick); };
}
