import { api } from '../api.js';
import { html, mount, icon, esc, raw, fmtTime, store, invalidate, toast } from '../util.js';

const SUGGESTIONS = [
  'Call these leads with my saved script: +91 98765 43210, +91 91234 56789',
  'What is the status of my latest campaign?',
  'Help me write a sales script for my product',
  "I'm uploading a lead list. Start a campaign for everyone in it.",
];
const MAX_KEEP = 60;

/** Minimal, safe Markdown: escape first, then add a few formats. */
function renderMarkdown(text) {
  const blocks = [];
  let src = esc(text || '').replace(/```([\s\S]*?)```/g, (_, code) => { blocks.push(code.replace(/^\w*\n/, '')); return `\u0000${blocks.length - 1}\u0000`; });
  src = src
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  let list = null;
  const flush = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${i}</li>`).join('')}</${list.tag}>`); list = null; } };
  for (const line of src.split('\n')) {
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const tag = ul ? 'ul' : 'ol';
      if (!list || list.tag !== tag) { flush(); list = { tag, items: [] }; }
      list.items.push((ul || ol)[1]);
      continue;
    }
    flush();
    if (line.trim()) out.push(`<p>${line.replace(/^#{1,6}\s*/, '')}</p>`);
  }
  flush();
  return out.join('').replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre>${blocks[Number(i)]}</pre>`);
}

export async function render(el, ctx) {
  const key = `nc.chat.${ctx.ws.org}.${ctx.ws.user}`;
  const saved = store(key) || {};
  const chat = { sessionId: saved.sessionId || null, messages: Array.isArray(saved.messages) ? saved.messages : [] };
  let file = null;
  let pending = null;

  ctx.setActions(html`<button class="btn sm" id="newChat">${icon('plus')}<span class="hide-sm">New conversation</span></button>`);

  mount(el, html`
    <div class="chat">
      <div class="chat-log" id="log" aria-live="polite"></div>
      <form class="composer" id="composer">
        <div id="fileRow" hidden></div>
        <label for="prompt" class="sr-only">Message Copilot</label>
        <textarea id="prompt" rows="2" placeholder="Tell NeuroCaller who to call, what to say, or ask about your campaigns"></textarea>
        <div class="bar">
          <label class="btn ghost sm" style="cursor:pointer" title="Attach a lead list, script, or FAQ">${icon('clip')}<span class="hide-sm">Attach</span><input type="file" id="file" accept=".csv,.xlsx,.xls,.pdf,.docx,.txt,.json" hidden></label>
          <span class="faint hide-sm" style="font-size:12px">Enter to send, Shift + Enter for a new line</span>
          <span style="margin-left:auto;display:flex;gap:8px"><button class="btn sm" type="button" id="stop" hidden>Stop waiting</button><button class="btn primary sm" type="submit" id="send">${icon('send')}Send</button></span>
        </div>
      </form>
    </div>`);

  const log = el.querySelector('#log');
  const prompt = el.querySelector('#prompt');
  const sendBtn = el.querySelector('#send');
  const stopBtn = el.querySelector('#stop');

  const persist = () => store(key, { sessionId: chat.sessionId, messages: chat.messages.slice(-MAX_KEEP) });

  const draw = () => {
    if (!chat.messages.length && !pending) {
      mount(log, html`
        <div style="display:grid;gap:14px;max-width:640px;padding-top:8px">
          <div class="msg agent"><div class="bubble">
            <p><strong>Hi, I'm your NeuroCaller copilot.</strong></p>
            <p>I can set up and launch calling campaigns from a chat. Paste phone numbers or attach a lead sheet, share your script or FAQ, and tell me when to start. You can also ask how a campaign is going.</p>
          </div></div>
          <div class="suggest">${SUGGESTIONS.map((s) => html`<button type="button" data-s="${s}">${s}</button>`)}</div>
        </div>`);
      return;
    }
    mount(log, html`${chat.messages.map((m) => html`
      <div class="msg ${m.role}">
        <div class="bubble">${m.role === 'agent' ? raw(renderMarkdown(m.text)) : html`${m.text}${m.file ? html`<br><span class="chip" style="margin-top:6px">${icon('clip', 'style="width:11px;height:11px"')}${m.file}</span>` : ''}`}</div>
        <span class="meta">${m.role === 'agent' ? 'NeuroCaller' : 'You'} · ${fmtTime(m.at)}${m.took ? ` · ${m.took}s` : ''}${m.error ? ' · not delivered' : ''}</span>
      </div>`)}
      ${pending ? html`<div class="msg agent"><div class="bubble typing" aria-label="NeuroCaller is working"><i></i><i></i><i></i></div><span class="meta" id="elapsed">Working…</span></div>` : ''}`);
    log.scrollTop = log.scrollHeight;
  };

  const setFile = (f) => {
    file = f;
    const row = el.querySelector('#fileRow');
    row.hidden = !f;
    if (f) {
      mount(row, html`<span class="file-chip">${icon('clip', 'style="width:13px;height:13px"')}${f.name}<button type="button" id="rmFile" aria-label="Remove attachment">${icon('x', 'style="width:13px;height:13px"')}</button></span>`);
      row.querySelector('#rmFile').addEventListener('click', () => { el.querySelector('#file').value = ''; setFile(null); });
    }
  };

  const send = async () => {
    const text = prompt.value.trim();
    if (!text && !file) { prompt.focus(); return; }
    if (pending) return;
    const query = text || `Please process the attached file: ${file.name}`;
    chat.messages.push({ role: 'user', text: query, at: new Date().toISOString(), file: file?.name });
    const form = new FormData();
    form.append('query', query);
    form.append('user_id', ctx.ws.user);
    form.append('org_id', ctx.ws.org);
    if (chat.sessionId) form.append('session_id', chat.sessionId);
    if (file) form.append('files', file, file.name);
    prompt.value = '';
    el.querySelector('#file').value = '';
    setFile(null);

    const controller = new AbortController();
    const started = Date.now();
    pending = { controller };
    sendBtn.disabled = true;
    stopBtn.hidden = false;
    draw();
    const timer = setInterval(() => { const e = el.querySelector('#elapsed'); if (e) e.textContent = `Working… ${Math.round((Date.now() - started) / 1000)}s`; }, 1000);

    try {
      const res = await api.chat(form, controller.signal);
      if (res?.session_id) chat.sessionId = res.session_id;
      chat.messages.push({ role: 'agent', text: res?.message || 'Done.', at: new Date().toISOString(), took: res?.time_taken });
      invalidate('campaigns');
    } catch (err) {
      const last = chat.messages[chat.messages.length - 1];
      if (last?.role === 'user') last.error = true;
      if (err.name === 'AbortError') toast('Stopped waiting. The agent may still finish in the background.', '');
      else chat.messages.push({ role: 'agent', text: `I couldn't complete that: ${err.message}`, at: new Date().toISOString() });
    } finally {
      clearInterval(timer);
      pending = null;
      persist();
      if (ctx.isCurrent()) { sendBtn.disabled = false; stopBtn.hidden = true; draw(); prompt.focus(); }
    }
  };

  el.querySelector('#composer').addEventListener('submit', (e) => { e.preventDefault(); send(); });
  prompt.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  prompt.addEventListener('input', () => { prompt.style.height = 'auto'; prompt.style.height = `${Math.min(200, prompt.scrollHeight)}px`; });
  el.querySelector('#file').addEventListener('change', (e) => setFile(e.target.files?.[0] || null));
  stopBtn.addEventListener('click', () => pending?.controller.abort());
  log.addEventListener('click', (e) => {
    const s = e.target.closest('[data-s]');
    if (s) { prompt.value = s.dataset.s; prompt.focus(); prompt.dispatchEvent(new Event('input')); }
  });
  document.getElementById('newChat').addEventListener('click', () => {
    if (pending) return;
    chat.sessionId = null;
    chat.messages = [];
    persist();
    draw();
    prompt.focus();
  });

  draw();
  prompt.focus();
  // A reply that arrives after leaving this screen is still saved to the conversation.
  return undefined;
}
