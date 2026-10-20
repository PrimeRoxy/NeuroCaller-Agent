// Thin client for the NeuroCaller FastAPI backend. Same origin, no build step.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = 'GET', query, json, form, signal } = {}) {
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    }
  }
  const options = { method, signal, headers: { Accept: 'application/json' } };
  if (form) {
    options.body = form;
  } else if (json !== undefined) {
    options.body = JSON.stringify(json);
    options.headers['Content-Type'] = 'application/json';
  }

  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(0, "Can't reach the NeuroCaller server. Check that it's running.");
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? (data.detail ?? data.message) : null;
    const message = typeof detail === 'string' ? detail : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  // Dashboard
  summary: (org, user) => request('/api/dashboard/stats/summary', { query: { org_id: org, user_id: user } }),
  campaigns: (org, user, status) => request('/api/dashboard/campaigns', { query: { org_id: org, user_id: user, status, limit: 100 } }),
  campaign: (id) => request(`/api/dashboard/campaign/${enc(id)}`),
  campaignProgress: (id) => request(`/api/dashboard/campaign/${enc(id)}/progress`),
  campaignCalls: (id) => request(`/api/dashboard/campaign/${enc(id)}/calls`),
  campaignEvents: (id) => request(`/api/dashboard/campaign/${enc(id)}/events`),
  campaignAction: (id, action) => request(`/api/dashboard/campaign/${enc(id)}/${action}`, { method: 'POST' }),

  // Calls
  calls: (org, user) => request('/calls/by-user-org', { query: { organisation_id: org, user_id: user } }),
  quickCall: (payload) => request('/quick-call', { method: 'POST', json: payload }),

  // Agent configuration
  config: (org, user) => request(`/api/allyroid/config/${enc(org)}`, { query: { user_id: user } }),
  saveConfig: (form) => request('/api/allyroid/config', { method: 'POST', form }),
  voices: (country) => request('/voices', { query: { country } }),
  rentedNumbers: (countryCode) => request('/rented-numbers', { query: { country_code: countryCode } }),

  // Scheduler
  schedulerTasks: () => request('/api/scheduler/tasks'),
  deleteSchedulerTask: (campaignId, toNumber) => request('/api/scheduler/tasks', { method: 'DELETE', query: { campaign_id: campaignId, to_number: toNumber } }),

  // Agent chat
  chat: (form, signal) => request('/salesmate', { method: 'POST', form, signal }),

  // Integrations and health
  salesforceCheck: (org, user) => request('/api/salesforce/check', { query: { organisation_id: org, user_id: user } }),
  health: () => request('/health'),
};

/**
 * Live call updates over /ws/live-calls. Reconnects with backoff.
 * Frames: {type:"snapshot", calls:[...]}, {type:"update", call:{...}}, {type:"ping"}.
 */
export function connectLiveCalls(org, user, { onSnapshot, onUpdate, onStatus }) {
  let socket = null;
  let closed = false;
  let attempt = 0;
  let retryTimer = null;

  const open = () => {
    if (closed) return;
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws/live-calls?org_id=${enc(org)}&user_id=${enc(user)}`;
    onStatus?.(attempt === 0 ? 'connecting' : 'reconnecting');
    socket = new WebSocket(url);
    socket.onopen = () => { attempt = 0; onStatus?.('live'); };
    socket.onmessage = (event) => {
      let frame;
      try { frame = JSON.parse(event.data); } catch { return; }
      if (frame.type === 'snapshot') onSnapshot?.(frame.calls || []);
      else if (frame.type === 'update' && frame.call) onUpdate?.(frame.call);
    };
    socket.onclose = () => {
      if (closed) return;
      onStatus?.('reconnecting');
      const delay = Math.min(30000, 1000 * 2 ** attempt++);
      retryTimer = setTimeout(open, delay);
    };
    socket.onerror = () => socket.close();
  };

  open();
  return () => {
    closed = true;
    clearTimeout(retryTimer);
    if (socket && socket.readyState <= 1) socket.close();
  };
}
