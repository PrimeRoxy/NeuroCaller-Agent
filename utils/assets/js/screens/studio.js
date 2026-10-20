import { api } from '../api.js';
import { html, mount, icon, fmtPhone, relTime, titleCase, loadingState, errorState, toast, invalidate } from '../util.js';

const COMMON_ZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Australia/Sydney', 'UTC'];

export async function render(el, ctx) {
  mount(el, loadingState(8));

  let config = null;
  try {
    config = await ctx.load.config(true);
  } catch (err) {
    if (err.status !== 404) {
      if (!ctx.isCurrent()) return undefined;
      mount(el, errorState(err));
      el.querySelector('[data-retry]')?.addEventListener('click', () => ctx.rerender());
      return undefined;
    }
  }
  if (!ctx.isCurrent()) return undefined;

  const cfg = config || {};
  const sf = cfg.salesforce_creds || {};
  const state = {
    voice: cfg.allyroid_voice || '',
    gender: cfg.allyroid_gender || '',
    numbers: [...(cfg.phone_numbers || [])],
    voiceRegion: 'IN',
    voiceGender: '',
  };
  const sameVoice = (v) => [v.name, v.voice_id].filter(Boolean).some((x) => x.toLowerCase() === String(state.voice).toLowerCase());
  let zones = COMMON_ZONES;
  try { zones = Intl.supportedValuesOf('timeZone'); } catch { /* older browser */ }
  if (cfg.timezone && !zones.includes(cfg.timezone)) zones = [cfg.timezone, ...zones];

  mount(el, html`
    <form id="studio" novalidate style="display:grid;gap:16px">
      ${!config ? html`<div class="confirm"><span style="flex:1"><b>Set up your agent</b><br>There's no agent saved for this workspace yet. Fill in the basics and save to create it.</span></div>` : ''}
      <div class="g">
        <section class="card s6"><div class="card-h"><h3>Identity</h3></div>
          <div class="form-grid">
            <div class="field"><label for="fName">Agent name</label><input class="input" id="fName" name="allyroid_name" value="${cfg.allyroid_name || ''}" placeholder="Aria" autocomplete="off"><span class="hint">How the agent introduces itself.</span></div>
            <div class="field"><label for="fOrg">Company name</label><input class="input" id="fOrg" name="organisation_name" value="${cfg.organisation_name || ''}" placeholder="Northwind Logistics" autocomplete="organization"></div>
          </div>
        </section>
        <section class="card s6"><div class="card-h"><h3>Calling hours</h3></div>
          <div class="form-grid">
            <div class="field full"><label for="fTz">Time zone</label><select class="input" id="fTz" name="timezone"><option value="">Not set</option>${zones.map((z) => html`<option value="${z}" ${z === cfg.timezone ? 'selected' : ''}>${z.replaceAll('_', ' ')}</option>`)}</select></div>
            <div class="field"><label for="fWs">Working hours start</label><input class="input mono" type="text" id="fWs" name="working_hours_start" value="${cfg.working_hours_start || ''}" placeholder="10:00 am" autocomplete="off"></div>
            <div class="field"><label for="fWe">Working hours end</label><input class="input mono" type="text" id="fWe" name="working_hours_end" value="${cfg.working_hours_end || ''}" placeholder="7:00 pm" autocomplete="off"></div>
            <div class="field"><label for="fQs">Quiet hours start</label><input class="input mono" type="text" id="fQs" name="quiet_hours_start" value="${cfg.quiet_hours_start || ''}" placeholder="9:00 pm" autocomplete="off"></div>
            <div class="field"><label for="fQe">Quiet hours end</label><input class="input mono" type="text" id="fQe" name="quiet_hours_end" value="${cfg.quiet_hours_end || ''}" placeholder="9:00 am" autocomplete="off"></div>
            <p class="hint full faint" style="font-size:12px">Use a time like 9:00 pm or 21:00. The agent won't dial during quiet hours; calls due then wait until they end.</p>
          </div>
        </section>

        <section class="card s12">
          <div class="card-h"><h3>Voice</h3><span class="chip" id="voicePicked">${state.voice ? `Selected: ${titleCase(state.voice)}` : 'No voice selected'}</span></div>
          <div class="toolbar" style="margin-bottom:12px">
            <div class="seg" role="group" aria-label="Voice catalogue" id="vRegion"><button type="button" data-v="IN" aria-pressed="true">India</button><button type="button" data-v="" aria-pressed="false">Global</button></div>
            <div class="seg" role="group" aria-label="Voice gender" id="vGender"><button type="button" data-g="" aria-pressed="true">All</button><button type="button" data-g="female" aria-pressed="false">Female</button><button type="button" data-g="male" aria-pressed="false">Male</button></div>
          </div>
          <div class="voice-grid" id="voices" role="radiogroup" aria-label="Agent voice">${loadingState(3)}</div>
        </section>

        <section class="card s8"><div class="card-h"><h3>Script and knowledge</h3><span class="faint mono" style="font-size:12px" id="scriptCount"></span></div>
          <div class="field"><label for="fScript" class="sr-only">Script</label>
            <textarea class="input" id="fScript" name="script" rows="14" style="min-height:280px" placeholder="Introduce yourself as Aria from Northwind. Ask how they handle new inbound leads today. If they're interested, offer a 20-minute demo this week. If they say they already have a tool, ask what they'd change about it.">${cfg.script || ''}</textarea>
            <span class="hint">What the agent should say, ask, and know: your offer, qualifying questions, and answers to common objections.</span>
          </div>
        </section>

        <div class="s4" style="display:grid;gap:16px;align-content:start">
          <section class="card"><div class="card-h"><h3>Phone numbers</h3></div>
            <div class="chips-input" id="numChips"></div>
            <div style="display:flex;gap:8px;margin-top:12px">
              <select class="input" id="numPick" aria-label="Add a rented number"><option value="">Loading numbers…</option></select>
              <button class="btn sm" type="button" id="numAdd">${icon('plus')}Add</button>
            </div>
            <span class="hint faint" style="font-size:12px;display:block;margin-top:8px">Numbers your agent calls from. Campaigns pick a free one automatically.</span>
          </section>
          <section class="card"><div class="card-h"><h3>CRM</h3></div>
            <div class="form-grid" style="grid-template-columns:minmax(0,1fr)">
              <div class="field"><label for="fCrm">Provider</label><select class="input" id="fCrm" name="crm_provider"><option value="">None</option><option value="salesforce" ${cfg.crm_provider === 'salesforce' ? 'selected' : ''}>Salesforce</option></select></div>
              <div id="sfFields" style="display:grid;gap:12px" ${cfg.crm_provider === 'salesforce' ? '' : 'hidden'}>
                <div class="field"><label for="fSfId">Client ID</label><input class="input mono" id="fSfId" name="salesforce_client_id" autocomplete="off" placeholder="${sf.client_id ? 'Saved. Leave blank to keep it.' : '3MVG9…'}"></div>
                <div class="field"><label for="fSfSecret">Client secret</label><input class="input mono" type="password" id="fSfSecret" name="salesforce_client_secret" autocomplete="new-password" placeholder="${sf.client_secret ? 'Saved. Leave blank to keep it.' : ''}"></div>
                <div class="field"><label for="fSfDomain">Domain</label><input class="input mono" id="fSfDomain" name="salesforce_domain" autocomplete="off" placeholder="${sf.domain || 'yourcompany.my.salesforce.com'}"></div>
                <div style="display:flex;gap:8px;align-items:center"><button class="btn sm" type="button" id="sfTest">Test connection</button><span id="sfResult" style="font-size:12.5px"></span></div>
              </div>
            </div>
          </section>
        </div>
      </div>
      <div class="save-bar"><span class="grow" id="saveState">${config?.updated_at ? `Last saved ${relTime(config.updated_at)}` : 'Not saved yet'}</span><button class="btn primary" type="submit" id="saveBtn">${config ? 'Save changes' : 'Create agent'}</button></div>
    </form>`);

  const form = el.querySelector('#studio');
  const $ = (sel) => el.querySelector(sel);
  let dirty = false;
  const markDirty = () => { dirty = true; $('#saveState').textContent = 'Unsaved changes'; };
  form.addEventListener('input', markDirty);

  // Script counter
  const script = $('#fScript');
  const countScript = () => { const w = script.value.trim() ? script.value.trim().split(/\s+/).length : 0; $('#scriptCount').textContent = `${w} words`; };
  script.addEventListener('input', countScript);
  countScript();

  // Voices
  const audio = new Audio();
  let playingBtn = null;
  const stopAudio = () => { audio.pause(); if (playingBtn) { playingBtn.classList.remove('playing'); playingBtn.innerHTML = icon('play').value; playingBtn = null; } };
  audio.addEventListener('ended', stopAudio);

  const drawVoices = async () => {
    const box = $('#voices');
    mount(box, loadingState(3));
    try {
      const voices = await ctx.load.voices(state.voiceRegion || undefined);
      if (!ctx.isCurrent()) return;
      const list = voices.filter((v) => !state.voiceGender || v.gender === state.voiceGender);
      const picked = state.voice && voices.find(sameVoice);
      if (picked) $('#voicePicked').textContent = `Selected: ${titleCase(picked.name)}`;
      if (!list.length) { mount(box, html`<p class="faint" style="font-size:13px">No voices in this filter.</p>`); return; }
      mount(box, list.map((v) => html`
        <div class="voice" role="radio" tabindex="0" aria-checked="${!!state.voice && sameVoice(v)}" data-name="${v.name}" data-value="${v.voice_id || v.name}" data-gender="${v.gender}">
          <button class="pb" type="button" data-src="${v.audio_url}" aria-label="Play ${v.name} sample">${icon('play')}</button>
          <span><b>${v.name}</b><span class="g">${v.gender}</span></span>
        </div>`));
    } catch (err) {
      mount(box, errorState(err));
      box.querySelector('[data-retry]')?.addEventListener('click', drawVoices);
    }
  };
  $('#voices').addEventListener('click', (e) => {
    const pb = e.target.closest('.pb');
    if (pb) {
      e.stopPropagation();
      if (playingBtn === pb) { stopAudio(); return; }
      stopAudio();
      audio.src = pb.dataset.src;
      audio.play().then(() => { playingBtn = pb; pb.classList.add('playing'); pb.innerHTML = icon('pause').value; })
        .catch(() => toast("Couldn't play this sample.", 'bad'));
      return;
    }
    const card = e.target.closest('.voice');
    if (!card) return;
    state.voice = card.dataset.value;
    state.gender = card.dataset.gender;
    el.querySelectorAll('.voice').forEach((v) => v.setAttribute('aria-checked', String(v === card)));
    $('#voicePicked').textContent = `Selected: ${titleCase(card.dataset.name)}`;
    markDirty();
  });
  $('#voices').addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('voice')) { e.preventDefault(); e.target.click(); } });
  const segToggle = (id, attr, key) => $(id).addEventListener('click', (e) => {
    const b = e.target.closest(`button[${attr}]`);
    if (!b) return;
    state[key] = b.getAttribute(attr);
    $(id).querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    drawVoices();
  });
  segToggle('#vRegion', 'data-v', 'voiceRegion');
  segToggle('#vGender', 'data-g', 'voiceGender');
  drawVoices();

  // Phone numbers
  const drawNumbers = () => {
    mount($('#numChips'), state.numbers.length
      ? state.numbers.map((n) => html`<span class="chip dat mono">${fmtPhone(n)}<button type="button" data-rm="${n}" aria-label="Remove ${n}">${icon('x')}</button></span>`)
      : html`<span class="faint" style="font-size:13px">No numbers yet.</span>`);
  };
  drawNumbers();
  $('#numChips').addEventListener('click', (e) => {
    const b = e.target.closest('[data-rm]');
    if (!b) return;
    state.numbers = state.numbers.filter((n) => n !== b.dataset.rm);
    drawNumbers(); markDirty();
  });
  api.rentedNumbers().then((res) => {
    if (!ctx.isCurrent()) return;
    const pick = $('#numPick');
    const nums = (res?.rented_numbers || []).map((n) => (typeof n === 'string' ? n : n.number || n.phone_number)).filter(Boolean);
    mount(pick, html`<option value="">${nums.length ? 'Choose a number' : 'No free numbers'}</option>${nums.map((n) => html`<option value="${n}">${fmtPhone(n)}</option>`)}`);
  }).catch(() => { if (ctx.isCurrent()) mount($('#numPick'), html`<option value="">Couldn't load numbers</option>`); });
  $('#numAdd').addEventListener('click', () => {
    const v = $('#numPick').value;
    if (!v) { toast('Pick a number to add.', 'bad'); return; }
    if (!state.numbers.includes(v)) { state.numbers.push(v); drawNumbers(); markDirty(); }
  });

  // CRM
  $('#fCrm').addEventListener('change', (e) => { $('#sfFields').hidden = e.target.value !== 'salesforce'; });
  $('#sfTest').addEventListener('click', async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    $('#sfResult').textContent = 'Checking…';
    $('#sfResult').style.color = 'var(--fg-3)';
    try {
      await api.salesforceCheck(ctx.ws.org, ctx.ws.user);
      $('#sfResult').textContent = 'Connected';
      $('#sfResult').style.color = 'var(--ok)';
    } catch (err) {
      $('#sfResult').textContent = dirty ? 'Save first, then test.' : err.message;
      $('#sfResult').style.color = 'var(--bad)';
    } finally { b.disabled = false; }
  });

  // Save
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#saveBtn');
    const changes = {};
    for (const name of ['allyroid_name', 'organisation_name', 'script', 'timezone', 'working_hours_start', 'working_hours_end', 'quiet_hours_start', 'quiet_hours_end', 'crm_provider']) {
      const value = form.elements[name].value.trim();
      if (value !== String(cfg[name] ?? '').trim()) changes[name] = value;
    }
    const badHour = ['working_hours_start', 'working_hours_end', 'quiet_hours_start', 'quiet_hours_end']
      .find((n) => changes[n] && !/^\d{1,2}\s*:\s*\d{2}\s*([ap]\.?m\.?)?$/i.test(changes[n]));
    if (badHour) { toast('Use a time like 9:00 pm or 21:00 for calling hours.', 'bad'); form.elements[badHour].focus(); return; }
    if (state.voice && state.voice !== (cfg.allyroid_voice || '')) { changes.allyroid_voice = state.voice; changes.allyroid_gender = state.gender; }
    if (JSON.stringify(state.numbers) !== JSON.stringify(cfg.phone_numbers || [])) changes.phone_numbers = [...state.numbers];
    for (const name of ['salesforce_client_id', 'salesforce_client_secret', 'salesforce_domain']) {
      const v = form.elements[name].value.trim();
      if (v) changes[name] = v;
    }
    if (config && !Object.keys(changes).length) { toast('Nothing to save'); return; }
    const data = new FormData();
    data.append('user_id', ctx.ws.user);
    data.append('organisation_id', ctx.ws.org);
    for (const [name, value] of Object.entries(changes)) {
      if (Array.isArray(value)) value.forEach((v) => data.append(name, v));
      else data.append(name, value);
    }
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      const res = await api.saveConfig(data);
      invalidate('config');
      for (const [name, value] of Object.entries(changes)) if (!name.startsWith('salesforce_')) cfg[name] = value;
      dirty = false;
      $('#saveState').textContent = 'Saved just now';
      btn.textContent = 'Save changes';
      ['salesforce_client_id', 'salesforce_client_secret'].forEach((n) => { form.elements[n].value = ''; });
      toast(res?.action === 'created' ? 'Agent created' : 'Changes saved', 'ok');
    } catch (err) {
      toast(err.message, 'bad');
      btn.textContent = config ? 'Save changes' : 'Create agent';
    } finally { btn.disabled = false; }
  });

  const beforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);
  return () => { stopAudio(); window.removeEventListener('beforeunload', beforeUnload); };
}
