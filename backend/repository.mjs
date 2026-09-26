import { config, storageMode } from './config.mjs';
import { demoStore } from './demo-store.mjs';

const tables = ['events', 'sessions', 'attendees', 'insights', 'share_links', 'transcripts'];

export async function createTranscript(payload) {
  const item = { id: `trn-${Date.now()}`, event_id: payload.event_id || 'evt-001', session_id: payload.session_id || 'ses-001', language: payload.language || 'auto', model: payload.model || '', text: payload.text || '', created_at: new Date().toISOString() };
  if (storageMode === 'supabase') {
    const response = await fetch(`${config.supabaseUrl}/rest/v1/transcripts`, { method: 'POST', headers: { apikey: config.supabaseKey, Authorization: `Bearer ${config.supabaseKey}`, 'Content-Type': 'application/json', Prefer: 'return=representation' }, body: JSON.stringify(item) });
    if (!response.ok) throw new Error(`Supabase ${response.status}`);
    return (await response.json())[0];
  }
  demoStore.transcripts.unshift(item);
  return item;
}

async function querySupabase(table) {
  const response = await fetch(`${config.supabaseUrl}/rest/v1/${table}?select=*`, {
    headers: { apikey: config.supabaseKey, Authorization: `Bearer ${config.supabaseKey}` }
  });
  if (!response.ok) throw new Error(`Supabase ${response.status}`);
  return response.json();
}

export async function list(table) {
  if (!tables.includes(table)) throw new Error(`Unknown resource: ${table}`);
  return storageMode === 'supabase' ? querySupabase(table) : demoStore[table];
}

export async function createSession(payload) {
  const item = { id: `ses-${Date.now()}`, event_id: 'evt-001', title: payload.title || 'New session', track: payload.track || 'New track', room: payload.room || 'TBD', speaker: payload.speaker || 'TBD', starts_at: payload.starts_at || new Date().toISOString(), ends_at: payload.ends_at || new Date(Date.now() + 45 * 60000).toISOString(), status: 'upcoming', attendance: 0, sentiment: .8, summary: 'Session brief will appear after the first live signals.' };
  if (storageMode === 'supabase') {
    const response = await fetch(`${config.supabaseUrl}/rest/v1/sessions`, { method: 'POST', headers: { apikey: config.supabaseKey, Authorization: `Bearer ${config.supabaseKey}`, 'Content-Type': 'application/json', Prefer: 'return=representation' }, body: JSON.stringify(item) });
    if (!response.ok) throw new Error(`Supabase ${response.status}`);
    return (await response.json())[0];
  }
  demoStore.sessions.push(item);
  return item;
}

export async function dashboard() {
  const [events, sessions, attendees, insights, share_links] = await Promise.all(['events', 'sessions', 'attendees', 'insights', 'share_links'].map(list));
  return { event: events[0], sessions, attendees, insights, share_links, mode: storageMode };
}
