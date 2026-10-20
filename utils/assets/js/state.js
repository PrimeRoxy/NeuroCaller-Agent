// Workspace identity, cached loaders, and the shared live-call feed.
import { api, connectLiveCalls } from './api.js';
import { cached, invalidate, store, toDate } from './util.js';

const WORKSPACE_KEY = 'nc.workspace';

export const workspace = {
  get() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = { org: params.get('org_id'), user: params.get('user_id') };
    if (fromUrl.org && fromUrl.user) {
      store(WORKSPACE_KEY, fromUrl);
      return fromUrl;
    }
    const saved = store(WORKSPACE_KEY);
    return saved?.org && saved?.user ? saved : null;
  },
  set(org, user) {
    store(WORKSPACE_KEY, { org, user });
    invalidate();
  },
  clear() {
    store(WORKSPACE_KEY, null);
    invalidate();
    const url = new URL(window.location.href);
    url.searchParams.delete('org_id');
    url.searchParams.delete('user_id');
    window.history.replaceState(null, '', url);
  },
};

const sortByStart = (a, b) => (toDate(b.start_time || b.updated_at)?.getTime() || 0) - (toDate(a.start_time || a.updated_at)?.getTime() || 0);

/** Loaders scoped to a workspace. Results are cached briefly so screens can share them. */
export function loaders({ org, user }) {
  const key = (name) => `${name}:${org}:${user}`;
  return {
    calls: (fresh) => {
      if (fresh) invalidate(key('calls'));
      return cached(key('calls'), 30_000, async () => {
        const res = await api.calls(org, user);
        return (res?.calls || []).slice().sort(sortByStart);
      });
    },
    summary: (fresh) => {
      if (fresh) invalidate(key('summary'));
      return cached(key('summary'), 30_000, () => api.summary(org, user));
    },
    campaigns: (fresh) => {
      if (fresh) invalidate(key('campaigns'));
      return cached(key('campaigns'), 15_000, async () => (await api.campaigns(org, user)) || []);
    },
    config: (fresh) => {
      if (fresh) invalidate(key('config'));
      return cached(key('config'), 60_000, () => api.config(org, user));
    },
    voices: (country) => cached(`voices:${country || 'default'}`, 10 * 60_000, () => api.voices(country)),
    schedulerTasks: (fresh) => {
      if (fresh) invalidate('scheduler');
      return cached('scheduler', 15_000, async () => (await api.schedulerTasks())?.tasks || []);
    },
  };
}

// ── Live feed (one socket for the whole app) ────────────────────────────────
const live = { calls: new Map(), status: 'idle', listeners: new Set(), stop: null, key: '' };

function emit() {
  for (const fn of live.listeners) fn(live);
}

export function startLive({ org, user }) {
  const key = `${org}:${user}`;
  if (live.stop && live.key === key) return;
  live.stop?.();
  live.calls.clear();
  live.key = key;
  live.stop = connectLiveCalls(org, user, {
    onStatus: (status) => { live.status = status; emit(); },
    onSnapshot: (calls) => {
      for (const call of calls) if (call.call_uuid) live.calls.set(call.call_uuid, { ...live.calls.get(call.call_uuid), ...call });
      emit();
    },
    onUpdate: (call) => {
      if (!call.call_uuid) return;
      const prev = live.calls.get(call.call_uuid);
      live.calls.set(call.call_uuid, { ...prev, ...call, _changed: Date.now() });
      // A call that just ended changes stats and call lists.
      if (call.hangup_cause && !prev?.hangup_cause) invalidate();
      emit();
    },
  });
}

export function stopLive() {
  live.stop?.();
  live.stop = null;
  live.key = '';
  live.calls.clear();
  live.status = 'idle';
}

export function onLive(fn) {
  live.listeners.add(fn);
  fn(live);
  return () => live.listeners.delete(fn);
}

export function liveCalls() {
  return [...live.calls.values()].sort((a, b) => (toDate(b.updated_at)?.getTime() || 0) - (toDate(a.updated_at)?.getTime() || 0));
}
