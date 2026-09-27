const state = { view: 'landing', data: null, authenticated: false, authMode: 'login', resetToken: '', currentSessionId: '', assets: [] };
window.selectAuthMode = kind => { state.authMode = kind === 'register' ? 'register' : 'login'; state.view = 'auth'; render(); };
let transcriptRefreshTimer = null;
const app = document.querySelector('#app');
const toast = document.querySelector('#toast');

const esc = (value = '') => String(value).replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
const date = iso => new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
const money = num => new Intl.NumberFormat('en', { notation:'compact', maximumFractionDigits:1 }).format(num);
function attendeePortalUrl(data = state.data) {
  const token = data?.share_links?.[0]?.token || 'gff-live';
  return `${location.origin}/?share=${encodeURIComponent(token)}#attendee`;
}
function activeSessionId() { return state.currentSessionId || state.data?.sessions?.find(session => session.status === 'live')?.id || state.data?.sessions?.[0]?.id || 'ses-001'; }
function notify(message) { toast.textContent = message; toast.classList.add('show'); setTimeout(() => toast.classList.remove('show'), 2600); }
function downloadCsv(filename, rows) { const source=rows||[]; if(!source.length)return notify('There is no data to export yet'); const keys=[...new Set(source.flatMap(row=>Object.keys(row)))]; const quote=value=>`"${String(Array.isArray(value)?value.join('; '):value??'').replaceAll('"','""')}"`; const content=[keys.join(','),...source.map(row=>keys.map(key=>quote(row[key])).join(','))].join('\n'); const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'})); const link=document.createElement('a'); link.href=url; link.download=filename; link.click(); URL.revokeObjectURL(url); notify(`${filename} downloaded`); }

async function api(path, options = {}, retry = false) {
  const stored = JSON.parse(localStorage.getItem('smart-event-session') || '{}');
  const headers = new Headers(options.headers || {});
  if (stored.access_token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${stored.access_token}`);
  const response = await fetch(path, {...options, headers});
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* non-JSON response */ }
  if (response.status === 401 && !retry && stored.refresh_token && !path.endsWith('/auth/refresh')) {
    const refreshed = await fetch('/api/auth/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({refresh_token:stored.refresh_token})});
    if (refreshed.ok) {
      const refreshPayload = await refreshed.json();
      localStorage.setItem('smart-event-session', JSON.stringify(refreshPayload.session || {}));
      return api(path, options, true);
    }
    localStorage.removeItem('smart-event-session');
    state.authenticated = false;
  }
  if (!response.ok) throw new Error(payload?.detail || payload?.error || 'Request failed');
  return payload;
}

// Browser speech playback for translated transcript results. The server still
// owns translation; this keeps audio playback private to the attendee's device.
const TRANSLATION_LOCALES = {English:'en-US', Spanish:'es-ES', French:'fr-FR', Japanese:'ja-JP', Arabic:'ar-SA', Portuguese:'pt-BR', Tamil:'ta-IN'};
const translationPlaybackObserver = new MutationObserver(() => {
  const output = document.querySelector('.translation-output');
  if (!output || output.querySelector('[data-action="speak-translation"]')) return;
  const kind = output.querySelector('.kind')?.textContent || 'English translation';
  const language = kind.replace(/\s+translation.*$/i, '').trim() || 'English';
  const paragraph = output.querySelector('p');
  if (!paragraph?.textContent?.trim()) return;
  window.__smartEventTranslation = {text: paragraph.textContent.trim(), language};
  output.insertAdjacentHTML('beforeend', '<button class="btn ghost listen-translation" data-action="speak-translation" type="button">▶ Listen</button>');
});
if (document.body) translationPlaybackObserver.observe(document.body, {childList:true, subtree:true});
document.addEventListener('click', event => {
  const button = event.target.closest('[data-action="speak-translation"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const item = window.__smartEventTranslation;
  if (!item?.text) return notify('Translate a transcript first');
  if (!('speechSynthesis' in window)) return notify('Audio playback is not supported in this browser');
  if (speechSynthesis.speaking) { speechSynthesis.cancel(); button.textContent = '▶ Listen'; return; }
  const utterance = new SpeechSynthesisUtterance(item.text);
  utterance.lang = TRANSLATION_LOCALES[item.language] || 'en-US';
  utterance.onend = () => { button.textContent = '▶ Listen'; };
  utterance.onerror = () => { button.textContent = '▶ Listen'; notify('Audio playback could not start'); };
  button.textContent = '■ Stop';
  speechSynthesis.speak(utterance);
}, true);
async function load() {
  // Render the public landing page immediately. The marketing page must not
  // disappear just because the organizer data/API is temporarily unavailable.
  state.authenticated = Boolean(localStorage.getItem('smart-event-session'));
  const recoveryParams = new URLSearchParams(location.hash.replace(/^#/, ''));
  const recoveryToken = recoveryParams.get('access_token') || new URLSearchParams(location.search).get('access_token');
  if (recoveryToken && (recoveryParams.get('type') === 'recovery' || location.hash.includes('type=recovery'))) {
    state.resetToken = recoveryToken;
    state.authMode = 'reset';
    state.view = 'auth';
    render();
    return;
  }
  // Do not render a data-dependent organizer view until the dashboard has
  // arrived; the first render must remain safe on a cold page load.
  const requestedView = location.hash.replace(/^#/, '').split('?')[0];
  state.view = ['auth', 'landing'].includes(requestedView) ? requestedView || 'landing' : 'landing';
  render();
  try {
    const shareToken = new URLSearchParams(location.search).get('share');
    if (shareToken) {
      const shared = await api(`/api/public/share/${encodeURIComponent(shareToken)}`);
      state.data = {event: shared.event || null, sessions: shared.sessions || [], share_links: shared.link ? [shared.link] : [], attendees: [], insights: [], transcripts: [], analytics: {}, mode: 'public'};
      state.view = 'attendee';
      history.replaceState({view: 'attendee'}, '', `${location.pathname}?share=${encodeURIComponent(shareToken)}#attendee`);
    } else if (state.authenticated) {
      state.data = await api('/api/dashboard');
      state.view = 'overview';
    }
    state.currentSessionId = state.data?.share_links?.[0]?.session_id || activeSessionId();
    if (state.view !== 'landing') render();
  } catch (error) {
    if (/session is invalid|session.*expired|organizer login is required/i.test(error.message)) {
      localStorage.removeItem('smart-event-session');
      state.authenticated = false;
      state.authMode = 'login';
      state.view = 'auth';
      render();
      notify('Your session expired. Please log in again.');
      return;
    }
    if (state.view !== 'landing' && state.view !== 'auth') {
      app.innerHTML = `<div class="empty">Could not load the event workspace.<br><small>${esc(error.message)}</small></div>`;
    }
  }
}
async function openWorkspace(message) {
  try {
    state.data = await api('/api/dashboard');
    state.view = 'overview';
    render();
    notify(message);
  } catch (error) {
    if (/session is invalid|session.*expired|organizer login is required/i.test(error.message)) {
      localStorage.removeItem('smart-event-session');
      state.authenticated = false;
      state.authMode = 'login';
      state.view = 'auth';
      render();
      notify('Your session expired. Please log in again.');
      return;
    }
    notify(`Workspace data is unavailable: ${error.message}`);
  }
}

function head(eyebrow, title, description, actions = '') { return `<div class="view-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${description}</p></div><div class="actions">${actions}</div></div>`; }
function landing() { return `<div class="marketing-page"><nav class="landing-nav"><a class="landing-brand" href="#landing"><span class="logo"><i></i></span><span>smart event <b>manager</b></span></a><div class="landing-links"><a href="#platform">Platform</a><a href="#workflow">Workflow</a><a href="#use-cases">Use cases</a><a href="#trust">Trust</a></div><div class="landing-actions"><button class="landing-login" data-view-link="auth" data-auth-mode="login">Log in</button><button class="landing-register" data-view-link="auth" data-auth-mode="register">Register</button><button class="btn lime" data-view-link="auth" data-auth-mode="login">Book a walkthrough ↗</button></div></nav><section class="landing-hero"><div class="hero-copy"><div class="eyebrow"><i></i> Event content intelligence</div><h1>Every voice.<br><span>One clear signal.</span></h1><p>Smart Event Manager captures the live room, turns conversations into intelligence, and keeps your event creating value long after the lights go down.</p><div class="actions"><button class="btn lime" data-view-link="auth" data-auth-mode="login">See it in action ↗</button><button class="landing-text-link" data-view-link="auth" data-auth-mode="register">Create your event workspace <span>↗</span></button></div><div class="hero-proof"><span class="proof-dot"></span> Trusted by teams running the world’s most important rooms <span class="proof-lines">01 · 02 · 03</span></div></div><div class="hero-visual"><div class="visual-glow"></div><div class="visual-card live-board"><div class="visual-top"><span>GLOBAL FUTURES FORUM</span><b>● LIVE</b></div><div class="visual-wave">${[30,52,38,75,45,90,62,78,40,86,58,72,48,95,57,80].map(h=>`<i style="height:${h}%"></i>`).join('')}</div><div class="visual-insight"><small>LIVE SYNTHESIS · 02:14 AGO</small><strong>Collective response is the new advantage.</strong><p>68 signals · 24 languages · 1,842 in the room</p></div></div><div class="floating-card float-a"><span>75+</span><small>LANGUAGES</small></div><div class="floating-card float-b"><span>20+</span><small>CONTENT FORMATS</small></div></div></section><section class="landing-strip"><span>Designed for the moments that matter</span><span>CONFERENCES</span><span>TRADE SHOWS</span><span>COMPANY EVENTS</span><span>ASSOCIATIONS</span></section><section class="landing-section" id="workflow"><div class="landing-section-head"><div><div class="eyebrow">The event content engine</div><h2>From live moment<br>to lasting momentum.</h2></div><p>One event creates hundreds of valuable moments. Capture them once, then make them work everywhere.</p></div><div class="landing-flow"><article><div class="flow-number">01</div><div class="flow-symbol">◌</div><h3>Capture</h3><p>Plug into your live audio, virtual stage, or event recording. Every voice becomes searchable signal.</p><a href="#platform">See live intelligence ↗</a></article><article><div class="flow-number">02</div><div class="flow-symbol">✦</div><h3>Synthesize</h3><p>Find the themes, questions, quotes, and decisions that deserve to move through your organization.</p><a href="#platform">See insights ↗</a></article><article><div class="flow-number">03</div><div class="flow-symbol">↗</div><h3>Remix</h3><p>Turn one session into recaps, briefs, speaker packs, social moments, and an attendee knowledge layer.</p><a href="#platform">See content studio ↗</a></article></div></section><section class="landing-section dark-section" id="platform"><div class="landing-section-head"><div><div class="eyebrow">One platform, every phase</div><h2>Make the room<br>work harder.</h2></div><p>Keep people present during the event, then give every idea a longer life after it.</p></div><div class="platform-grid"><div class="platform-feature feature-large"><span class="feature-tag">LIVE</span><h3>Intelligence while it’s happening.</h3><p>Live summaries, transcript, translation, audience questions, and signal maps — without pulling people out of the moment.</p><div class="feature-orbit"></div></div><div class="platform-feature feature-lilac"><span class="feature-tag">INSIGHTS</span><h3>Clarity for every stakeholder.</h3><p>Executive reports, theme clusters, intent signals, and sponsor-ready proof.</p></div><div class="platform-feature feature-lime"><span class="feature-tag">REMIX</span><h3>Content that keeps going.</h3><p>Branded portals, social-ready moments, speaker packs, and follow-up journeys.</p></div></div></section><section class="landing-section" id="use-cases"><div class="landing-section-head"><div><div class="eyebrow">Built around the way you work</div><h2>Your event.<br>Your signal.</h2></div><p>From a 50-person summit to a multi-stage conference, Smart Event Manager adapts to the room.</p></div><div class="case-grid"><div><span>01</span><h3>Keep attendees present</h3><p>Give people the takeaways without making them choose between the stage and their notes.</p></div><div><span>02</span><h3>Prove event value</h3><p>Show leadership and sponsors what resonated, what connected, and what moved next.</p></div><div><span>03</span><h3>Extend the event</h3><p>Turn three days of energy into a knowledge layer that works for the next 362.</p></div></div></section><section class="landing-cta" id="trust"><div><div class="eyebrow">Ready when the room is</div><h2>Don’t let the good part end at goodbye.</h2><p>See the full Smart Event Manager workflow in your next event.</p></div><button class="btn lime" data-view-link="auth" data-auth-mode="login">Book a walkthrough ↗</button></section><footer class="landing-footer"><span>© 2026 Smart Event Manager</span><div><a href="#workflow">Workflow</a><a href="#platform">Platform</a><a href="#use-cases">Use cases</a><button data-view-link="auth" data-auth-mode="login">Organizer access ↗</button></div></footer></div>`; }
function overview() {
  const d = state.data, eventName = d.event?.name || 'Your event', live = d.sessions.find(s => s.status === 'live') || d.sessions[0] || {attendance: 0, title: 'No live session yet'};
  return `${head('Saturday · September 26, 2026', 'Good morning, Leila.', `Your command center for every moment before, during, and after ${esc(eventName)}.`, '<button class="btn ghost" data-action="share">↗ Share portal</button><button class="btn lime" data-action="add-session">+ Add session</button>')}
  <div class="grid kpis"><div class="card kpi"><small>Checked in</small><strong>${money(d.attendees.filter(a=>a.checked_in).length * 1000 + 842)}</strong><footer><span class="up">↗ 12.4%</span><span>vs. last event</span></footer></div><div class="card kpi"><small>Live attendance</small><strong>${money(live.attendance)}</strong><footer><span class="up">↗ 8.1%</span><span>${live.title}</span></footer></div><div class="card kpi"><small>Content signals</small><strong>68</strong><footer><span class="up">↗ 14 new</span><span>this session</span></footer></div><div class="card kpi"><small>Intent score</small><strong>82%</strong><footer><span class="up">Healthy</span><span>across attendees</span></footer></div></div>
  <div class="grid overview-grid"><section class="card live-card"><div class="card-head"><h2>Run of show</h2><a href="#sessions" data-view-link="sessions">View full agenda ↗</a></div><div class="timeline">${d.sessions.map(s=>`<div class="timeline-row"><span class="time">${date(s.starts_at)}</span><span class="dot ${s.status==='live'?'live':''}"></span><div><strong>${esc(s.title)}</strong><small>${esc(s.track)} · ${esc(s.room)}</small></div><span class="session-status ${s.status==='live'?'live':''}">${s.status==='live'?'Live now':'Upcoming'}</span></div>`).join('')}</div></section>
  <section class="card"><div class="card-head"><h2>Live intelligence</h2><a href="#content" data-view-link="content">Open studio ↗</a></div><div class="insight-list">${d.insights.map(i=>`<div class="insight"><span class="kind">${esc(i.kind)} · ${Math.round(i.confidence*100)}% confidence</span><strong>${esc(i.title)}</strong><p>${esc(i.body)}</p></div>`).join('')}<div class="signal">${[32,48,29,66,53,82,58,91,47,77,60,86,39,72,65,93,55,68].map(h=>`<i style="height:${h}%"></i>`).join('')}</div></div></section></div>`;
}
function sessions() { const d = state.data; return `${head('Program builder', 'Sessions', 'Keep the agenda clear, the rooms moving, and every talk ready to become a useful takeaway.', '<button class="btn ghost" data-action="export">↓ Export agenda</button><button class="btn lime" data-action="add-session">+ Add session</button>')}<section class="card section-card"><div class="card-head"><h2>All sessions · ${d.sessions.length}</h2><a href="#overview" data-view-link="overview">Back to overview</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Session</th><th>Track / room</th><th>Speaker</th><th>Time</th><th>Attendance</th><th>Status</th></tr></thead><tbody>${d.sessions.map(s=>`<tr><td><strong>${esc(s.title)}</strong><br><span class="muted">${esc(s.summary)}</span></td><td>${esc(s.track)}<br>${esc(s.room)}</td><td>${esc(s.speaker)}</td><td>${date(s.starts_at)}<br>${date(s.ends_at)}</td><td>${money(s.attendance)}</td><td><span class="badge ${s.status==='live'?'orange':'green'}">${s.status}</span></td></tr>`).join('')}</tbody></table></div></section>`; }
function sessionList() { const d = state.data; return `${head('Program builder', 'Sessions', 'Keep the agenda clear, the rooms moving, and every talk ready to become a useful takeaway.', '<button class="btn ghost" data-action="export">↓ Export agenda</button><button class="btn lime" data-action="add-session">+ Add session</button>')}<section class="card section-card"><div class="card-head"><h2>All sessions · ${d.sessions.length}</h2><a href="#overview" data-view-link="overview">Back to overview</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Session</th><th>Track / room</th><th>Speaker</th><th>Time</th><th>Attendance</th><th>Status</th><th>Manage</th></tr></thead><tbody>${d.sessions.map(s=>`<tr><td><strong>${esc(s.title)}</strong><br><span class="muted">${esc(s.summary)}</span></td><td>${esc(s.track)}<br>${esc(s.room)}</td><td>${esc(s.speaker)}</td><td>${date(s.starts_at)}<br>${date(s.ends_at)}</td><td>${money(s.attendance)}</td><td><span class="badge ${s.status==='live'?'orange':'green'}">${esc(s.status)}</span></td><td><div class="actions-row compact-actions">${s.status==='live'?`<button class="btn lime" data-action="stop-session" data-session-id="${esc(s.id)}">Stop live</button>`:s.status!=='completed'?`<button class="btn lime" data-action="start-session" data-session-id="${esc(s.id)}">Start live</button>`:''}<button class="btn ghost" data-action="edit-session" data-session-id="${esc(s.id)}">Edit</button><button class="btn ghost" data-action="duplicate-session" data-session-id="${esc(s.id)}">Duplicate</button><button class="btn ghost" data-action="delete-session" data-session-id="${esc(s.id)}">Delete</button></div></td></tr>`).join('')}</tbody></table></div></section>`; }
sessions = sessionList;
function attendees() { const d = state.data; return `${head('Audience intelligence', 'Attendees', 'Understand who is in the room, what they care about, and where the next meaningful connection can happen.', '<button class="btn ghost" data-action="export">↓ Export CSV</button><button class="btn purple" data-action="match">✦ Find matches</button>')}<section class="card section-card"><div class="card-head"><h2>Attendee signal map · ${d.attendees.length} loaded</h2><a href="#connect" data-view-link="connect">Networking tools ↗</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Person</th><th>Company / role</th><th>Interests</th><th>Intent score</th><th>Check-in</th></tr></thead><tbody>${d.attendees.map(a=>`<tr><td><strong>${esc(a.full_name)}</strong></td><td>${esc(a.company)}<br>${esc(a.role)}</td><td>${a.interests.map(i=>`<span class="badge">${esc(i)}</span>`).join(' ')}</td><td class="score">${a.intent_score}</td><td><span class="badge ${a.checked_in?'green':''}">${a.checked_in?'Checked in':'Not yet'}</span></td></tr>`).join('')}</tbody></table></div></section>`; }
function attendeeListWithCheckin() { const d = state.data; return `${head('Audience intelligence', 'Attendees', 'Understand who is in the room, what they care about, and where the next meaningful connection can happen.', '<button class="btn ghost" data-action="export">↓ Export CSV</button><button class="btn purple" data-action="match">✦ Find matches</button>')}<section class="card section-card"><div class="card-head"><h2>Attendee signal map · ${d.attendees.length} loaded</h2><a href="#connect" data-view-link="connect">Networking tools ↗</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Person</th><th>Company / role</th><th>Interests</th><th>Intent score</th><th>Check-in</th><th>Action</th></tr></thead><tbody>${d.attendees.map(a=>`<tr><td><strong>${esc(a.full_name)}</strong></td><td>${esc(a.company)}<br>${esc(a.role)}</td><td>${a.interests.map(i=>`<span class="badge">${esc(i)}</span>`).join(' ')}</td><td class="score">${a.intent_score}</td><td><span class="badge ${a.checked_in?'green':''}">${a.checked_in?'Checked in':'Not yet'}</span></td><td><button class="btn ghost" data-action="check-in" data-attendee-id="${esc(a.id)}" data-checked="${a.checked_in ? 'true' : 'false'}">${a.checked_in ? 'Undo' : 'Check in'}</button></td></tr>`).join('')}</tbody></table></div></section>`; }
attendees = attendeeListWithCheckin;
function content() { return `${head('Content engine', 'Content studio', 'Turn live signals into a clean stream of assets for attendees, speakers, sponsors, and leadership.', '<button class="btn ghost" data-action="share">↗ Share selected</button><button class="btn lime" data-action="generate" data-content-type="attendee_recap" data-content-title="Attendee recap">✦ Generate recap</button>')}<div class="grid studio-grid"><article class="card studio-card purple"><span class="studio-icon">✦</span><h3>Executive brief</h3><p>One sharp page of the themes, tensions, and decisions that leadership needs next.</p><button class="btn lime" data-action="generate" data-content-type="executive_brief" data-content-title="Executive brief">Generate brief ↗</button></article><article class="card studio-card lime"><span class="studio-icon">◌</span><h3>Attendee recap</h3><p>A branded, multilingual digest that helps the room keep learning after the closing keynote.</p><button class="btn" data-action="generate" data-content-type="attendee_recap" data-content-title="Attendee recap">Create recap ↗</button></article><article class="card studio-card blue"><span class="studio-icon">◒</span><h3>Speaker remix</h3><p>Give every speaker a quote bank, session summary, and moments ready for social.</p><button class="btn ghost" data-action="generate" data-content-type="speaker_pack" data-content-title="Speaker remix">Build speaker pack ↗</button></article><article class="card studio-card orange"><span class="studio-icon">↗</span><h3>Social carousel</h3><p>Turn one event signal into an eight-slide story with a ready-to-post caption.</p><button class="btn" data-action="generate" data-content-type="social_carousel" data-content-title="Social carousel">Create carousel ↗</button></article><article class="card studio-card lilac"><span class="studio-icon">✉</span><h3>Follow-up email</h3><p>Give attendees a clear recap, practical next step, and reason to stay connected.</p><button class="btn ghost" data-action="generate" data-content-type="followup_email" data-content-title="Follow-up email">Draft email ↗</button></article><article class="card studio-card"><span class="studio-icon">▦</span><h3>Signal library</h3><p>Browse the strongest quotes, audience questions, topic clusters, and audio moments.</p><button class="btn ghost" data-action="library">Open library ↗</button></article></div>`; }
function connect() { const d = state.data, link = attendeePortalUrl(d); const qr = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(link)}`; return `${head('Distribution layer', 'Share & connect', 'Give every audience a doorway into the event — on stage, in the app, or in the follow-up email.', '<button class="btn lime" data-action="copy-link">Copy attendee link</button>')}<div class="grid connect-grid"><section class="card share-card"><h3>Attendee portal</h3><p>One clean destination for live takeaways, session details, questions, and post-event content.</p><div class="link-row"><input readonly value="${link}" id="share-link"/><button class="btn ghost" data-action="copy-link">Copy</button></div><div class="qr-wrap"><img class="qr-image" src="${qr}" alt="QR code for attendee portal"/><small>Place this QR on badges, screens, print, and speaker slides.<br><br><strong>${d.share_links?.[0]?.clicks || 0} portal opens</strong> from the current link.</small></div><div class="actions-row"><a class="btn" href="${link}">Open attendee portal ↗</a><button class="btn ghost" data-action="open-screen">Big-screen mode</button></div></section><section class="card share-card"><h3>Connected workflow</h3><p>Keep the event ecosystem moving with simple handoffs.</p><div class="insight"><span class="kind">LIVE DATA</span><strong>Supabase-ready data layer</strong><p>Events, sessions, attendees, insights, and share links are modeled for a direct database connection.</p></div><div class="insight"><span class="kind">EMBED KIT</span><strong>Put it inside your own app</strong><p>Use the portal URL inside an iframe, WebView, QR badge, email CTA, or event app deep link.</p></div><div class="actions-row"><button class="btn ghost" data-view-link="transcript">Live transcript</button><button class="btn ghost" data-action="copy-embed">Copy embed code</button></div></section></div>`; }
function attendee() { const d=state.data, link=attendeePortalUrl(d); return `<div class="attendee-view"><div class="attendee-nav"><a class="brand attendee-brand" href="#overview"><span class="logo"><i></i></span><span>smart event <b>manager</b></span></a><span class="attendee-live"><i></i> LIVE EXPERIENCE</span><button class="btn ghost" data-view-link="overview">Organizer view</button></div><div class="attendee-hero"><div><div class="eyebrow">Global Futures Forum · Singapore</div><h1>Stay in the room.<br><span>Take the insight with you.</span></h1><p>Live takeaways, language support, audience questions, and the moments worth sharing — all in one event companion.</p><div class="actions"><button class="btn lime" data-view-link="transcript">Open live transcript</button><button class="btn ghost" data-action="share-social">Share event ↗</button></div></div><div class="attendee-card"><div class="card-head"><h2>Now on stage</h2><span class="badge orange">LIVE</span></div><div class="attendee-session"><small>MAIN STAGE · SESSION 04</small><strong>Designing for what’s next</strong><span>Maya Chen · Future Systems</span></div><div class="translation-row"><span>◎ English</span><select id="language"><option>English</option><option>Spanish</option><option>French</option><option>Japanese</option><option>Arabic</option></select><button class="btn purple" data-view-link="transcript">Translate</button></div></div></div><div class="attendee-tiles"><article><span>✦</span><h3>Live takeaways</h3><p>Keep up with the strongest ideas even when you move between stages.</p></article><article><span>◌</span><h3>Ask the room</h3><p>Submit questions and see what the audience is curious about right now.</p></article><article><span>↗</span><h3>Make it travel</h3><p>Share a quote, recap, or social-ready moment with your network.</p></article></div><div class="attendee-footer"><span>Event link: ${esc(link)}</span><button class="btn ghost" data-action="share-social">Share to social ↗</button></div></div>`; }
function screen() { const d=state.data, live=d.sessions.find(s=>s.status==='live'); return `<div class="screen-view"><div class="screen-top"><span class="brand attendee-brand"><span class="logo"><i></i></span> smart event manager</span><span><i class="screen-dot"></i> LIVE · GLOBAL FUTURES FORUM</span></div><div class="screen-content"><div class="eyebrow">MAIN STAGE · SESSION 04</div><h1>${esc(live.title)}</h1><p>${esc(live.summary)}</p><div class="screen-signal">${[40,62,30,82,54,76,46,90,61,72,53,86,68,96,50,77].map(h=>`<i style="height:${h}%"></i>`).join('')}</div><div class="screen-bottom"><span>Ask a question · Scan to join · 75+ languages</span><button class="btn lime" data-view-link="attendee">Open attendee portal ↗</button></div></div></div>`; }
function transcript() { const d=state.data; return `<div class="transcript-view">${head('Live language layer', 'Live transcript', 'Turn every spoken moment into readable, searchable intelligence for every attendee.', '<button class="btn ghost" data-view-link="attendee">Attendee view</button><button class="btn lime" data-action="copy-link">Share transcript</button>')}<div class="transcript-toolbar card"><span class="badge orange">● LIVE</span><strong>Designing for what’s next</strong><span class="muted">Maya Chen · Main stage</span><select><option>English · Original</option><option>Spanish · Auto-translated</option><option>French · Auto-translated</option><option>Japanese · Auto-translated</option></select></div><div class="transcript-grid"><section class="card transcript-card"><div class="transcript-line"><span>09:32:18</span><p>We often ask whether an organization can predict what is coming next.</p></div><div class="transcript-line active"><span>09:32:31</span><p>But the stronger question is whether we have built the habit of responding together.</p></div><div class="transcript-line"><span>09:32:47</span><p>That is what turns uncertainty from a threat into a shared design problem.</p></div><div class="transcript-line"><span>09:33:02</span><p>And it is a practice every team can start today.</p></div></section><aside class="card translation-card"><div class="card-head"><h2>Language coverage</h2></div><div class="language-list">${['English','Spanish','French','Japanese','Arabic','Portuguese'].map((l,i)=>`<div><span>${l}</span><b>${i<3?'Live':'Queued'}</b></div>`).join('')}</div></aside></div></div>`; }
function auth() { return `<div class="auth-view"><div class="auth-art"><div class="auth-orbit"></div><div class="eyebrow">Organizer workspace</div><h1>Run the room.<br><span>Keep the signal.</span></h1><p>Smart Event Manager gives event teams one place to plan, operate, share, and prove the value of every live moment.</p><div class="auth-proof"><span>◈</span> Live intelligence <span>✦</span> Shareable content <span>◎</span> Global access</div></div><div class="auth-card card"><div class="auth-tabs"><button type="button" class="auth-tab active" data-auth-tab="login" onclick="window.selectAuthMode('login')">Log in</button><button type="button" class="auth-tab" data-auth-tab="register" onclick="window.selectAuthMode('register')">Create account</button></div><div id="auth-form-wrap"></div></div></div>`; }
function authForm(kind='login') { const config={login:['Welcome back','Log in to your organizer workspace','Log in'],register:['Create your workspace','Start building your next great event','Create account'],forgot:['Reset your password','We’ll send a secure reset link to your inbox','Send reset link']}[kind]; const password=kind!=='forgot'?`<label>Password<input type="password" name="password" placeholder="At least 8 characters" required></label>${kind==='login'?'<button type="button" class="forgot-inline-toggle" data-action="forgot-inline" style="border:0;background:none;color:var(--purple);font-size:11px;font-weight:800;padding:7px 0 0;text-align:left">Forgot password?</button><div class="forgot-inline" hidden><label>Reset email<input type="email" name="resetEmail" placeholder="you@company.com"></label><button type="button" class="btn ghost reset-inline-button" data-action="send-reset" style="margin-top:6px">Send reset link</button></div>':''}`:''; return `<form class="auth-form" id="${kind}-form"><h2>${config[0]}</h2><p>${config[1]}</p><label>Email address<input type="email" name="email" placeholder="you@company.com" required></label>${password}${kind==='register'?'<label>Confirm password<input type="password" name="confirm" placeholder="Repeat your password" required></label>':''}<button class="btn purple">${config[2]} ↗</button><small>By continuing, you agree to the workspace terms and event data policy.</small></form>`; }
function resetAuthForm() { return `<form class="auth-form" id="reset-form"><h2>Choose a new password</h2><p>Create a new secure password for your organizer account.</p><label>New password<input type="password" name="password" placeholder="At least 8 characters" required></label><label>Confirm password<input type="password" name="confirm" placeholder="Repeat your password" required></label><button class="btn purple">Update password ?</button><small>Your recovery link is used only for this password update.</small></form>`; }
function render() { const views = { landing, overview, sessions, attendees, content, connect, attendee, screen, transcript, auth }; app.innerHTML = views[state.view](); const fullBleed = ['landing','auth','attendee','screen','transcript'].includes(state.view); document.body.classList.toggle('marketing', fullBleed); document.body.classList.toggle('workspace-view', !fullBleed); document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active', b.dataset.view===state.view)); if(state.view==='auth'){const wrap=document.querySelector('#auth-form-wrap');wrap.innerHTML=state.authMode==='reset'?resetAuthForm():authForm(state.authMode);document.querySelectorAll('[data-auth-tab]').forEach(tab=>tab.addEventListener('click',()=>{state.authMode=tab.dataset.authTab;document.querySelectorAll('[data-auth-tab]').forEach(x=>x.classList.toggle('active',x===tab));wrap.innerHTML=authForm(state.authMode)}));} }
function modal() { if (document.querySelector('.modal-backdrop')) return; document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Add to the live program</div><h2>Create a session</h2><p>New sessions are saved to the local demo store, or directly to Supabase when your environment keys are configured.</p><form class="form-grid" id="session-form"><label>Session title<input name="title" required placeholder="e.g. Designing for the next decade"/></label><label>Track<select name="track"><option>Main stage</option><option>Leadership</option><option>Growth</option></select></label><label>Speaker<input name="speaker" placeholder="Name · Company"/></label><label>Room<input name="room" placeholder="Room 101"/></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button><button class="btn lime">Create session</button></div></form></div></div>`); }
function eventModal(mode = 'edit') {
  document.querySelector('.modal-backdrop')?.remove();
  const event = state.data?.event || {};
  const editing = mode === 'edit';
  const dateValue = value => String(value || '').replace(/([+-]\d\d:\d\d|Z)$/, '').slice(0, 16);
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Event workspace</div><h2>${editing ? 'Manage event' : 'Create an event'}</h2><p>${editing ? 'Update the event identity used across the attendee portal, QR link, and content workflow.' : 'Start a separate event workspace for a new program.'}</p><form class="form-grid" id="event-form"><input type="hidden" name="mode" value="${editing ? 'edit' : 'create'}"><input type="hidden" name="event_id" value="${esc(event.id || '')}"><label>Event name<input name="name" required value="${editing ? esc(event.name || '') : ''}" placeholder="e.g. Global Futures Forum"></label><label>Public slug<input name="slug" required value="${editing ? esc(event.slug || '') : ''}" placeholder="global-futures-forum"></label><label>Venue<input name="venue" value="${editing ? esc(event.venue || '') : ''}" placeholder="City · venue"></label><label>Starts<input type="datetime-local" name="starts_at" value="${editing ? dateValue(event.starts_at) : ''}" required></label><label>Ends<input type="datetime-local" name="ends_at" value="${editing ? dateValue(event.ends_at) : ''}" required></label><label>Status<select name="status"><option value="draft" ${event.status === 'draft' ? 'selected' : ''}>Draft</option><option value="scheduled" ${event.status === 'scheduled' ? 'selected' : ''}>Scheduled</option><option value="live" ${event.status === 'live' ? 'selected' : ''}>Live</option><option value="completed" ${event.status === 'completed' ? 'selected' : ''}>Completed</option></select></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button>${editing ? '<button type="button" class="btn ghost" data-action="event-new">New event</button>' : ''}<button class="btn lime">${editing ? 'Save event' : 'Create event'} ↗</button></div></form></div></div>`);
}
function syncEventChrome() { const name = state.data?.event?.name || 'Global Futures Forum'; document.querySelectorAll('[data-event-name]').forEach(node => { node.textContent = name; }); }
const originalEventModal = eventModal;
eventModal = function eventModalWithChoices(mode = 'edit') {
  originalEventModal(mode);
  let choices = document.querySelector('#event-choices');
  if (!choices) {
    const form = document.querySelector('#event-form');
    form?.insertAdjacentHTML('beforebegin', '<div id="event-choices" class="insight-list"><span class="muted">Loading event workspaces…</span></div>');
    choices = document.querySelector('#event-choices');
  }
  if (!choices) return;
  api('/api/events').then(events => {
    choices.innerHTML = events.length ? `<div class="eyebrow">Switch event</div>${events.map(item => `<button type="button" class="btn ghost" data-event-switch="${esc(item.id)}" style="margin:5px 5px 0 0">${esc(item.name || item.title || 'Untitled event')}</button>`).join('')}` : '<span class="muted">No other event workspaces yet.</span>';
  }).catch(error => { choices.innerHTML = `<span class="muted">${esc(error.message)}</span>`; });
};
// Public surfaces must follow the selected event workspace instead of showing demo-only copy.
attendee = function dynamicAttendee() {
  const d = state.data || {};
  const event = d.event || {};
  const live = (d.sessions || []).find(session => session.status === 'live') || (d.sessions || [])[0] || {
    title: 'Event session', speaker: 'Speaker to be announced', room: 'Main stage'
  };
  const eventName = event.name || 'Your event';
  const link = `${location.origin}/attendee/${event.slug || 'event'}`;
  return `<div class="attendee-view"><div class="attendee-nav"><a class="brand attendee-brand" href="#overview"><span class="logo"><i></i></span><span>smart event <b>manager</b></span></a><span class="attendee-live"><i></i> LIVE EXPERIENCE</span><button class="btn ghost" data-view-link="overview">Organizer view</button></div><div class="attendee-hero"><div><div class="eyebrow">${esc(eventName)}${event.venue ? ` · ${esc(event.venue)}` : ''}</div><h1>Stay in the room.<br><span>Take the insight with you.</span></h1><p>Live takeaways, language support, audience questions, and the moments worth sharing — all in one event companion.</p><div class="actions"><button class="btn lime" data-view-link="transcript">Open live transcript</button><button class="btn ghost" data-action="share-social">Share event ↗</button></div></div><div class="attendee-card"><div class="card-head"><h2>Now on stage</h2><span class="badge orange">LIVE</span></div><div class="attendee-session"><small>${esc(live.track || 'MAIN STAGE')} · ${esc(live.room || 'LIVE ROOM')}</small><strong>${esc(live.title || 'Event session')}</strong><span>${esc(live.speaker || 'Speaker to be announced')}</span></div><div class="translation-row"><span>◎ English</span><select id="language"><option>English</option><option>Spanish</option><option>French</option><option>Japanese</option><option>Arabic</option></select><button class="btn purple" data-view-link="transcript">Translate</button></div></div></div><div class="attendee-tiles"><article><span>✦</span><h3>Live takeaways</h3><p>Keep up with the strongest ideas even when you move between stages.</p></article><article><span>◌</span><h3>Ask the room</h3><p>Submit questions and see what the audience is curious about right now.</p></article><article><span>↗</span><h3>Make it travel</h3><p>Share a quote, recap, or social-ready moment with your network.</p></article></div><div class="attendee-footer"><span>Event link: ${esc(link)}</span><button class="btn ghost" data-action="share-social">Share to social ↗</button></div></div>`;
};
screen = function dynamicScreen() {
  const d = state.data || {};
  const live = (d.sessions || []).find(session => session.status === 'live') || (d.sessions || [])[0] || { title: 'Welcome to the event', summary: 'Live session intelligence will appear here.', track: 'Main stage', room: 'Live room' };
  const eventName = d.event?.name || 'Your event';
  return `<div class="screen-view"><div class="screen-top"><span class="brand attendee-brand"><span class="logo"><i></i></span> smart event manager</span><span><i class="screen-dot"></i> LIVE · ${esc(eventName)}</span></div><div class="screen-content"><div class="eyebrow">${esc(live.track || 'MAIN STAGE')} · ${esc(live.room || 'LIVE ROOM')}</div><h1>${esc(live.title || 'Event session')}</h1><p>${esc(live.summary || 'Live session intelligence will appear here.')}</p><div class="screen-signal">${[40,62,30,82,54,76,46,90,61,72,53,86,68,96,50,77].map(h => `<i style="height:${h}%"></i>`).join('')}</div><div class="screen-bottom"><span>Ask a question · Scan to join · 75+ languages</span><button class="btn lime" data-view-link="attendee">Open attendee portal ↗</button></div></div></div>`;
};
function teamPanelMarkup(team) {
  const members = (team.members || []).map(member => `<article class="insight team-member"><div><span class="kind">${esc(member.status || 'active')}</span><strong>${esc(member.full_name || member.email || member.user_id || 'Workspace member')}</strong><small class="muted">${esc(member.email || '')}</small></div><select data-team-role="${esc(member.id || member.user_id || '')}" aria-label="Role for ${esc(member.full_name || member.email || 'member')}">${['organization_admin','event_organizer','content_editor','speaker','attendee'].map(role => `<option value="${role}" ${member.role === role ? 'selected' : ''}>${role.replaceAll('_', ' ')}</option>`).join('')}</select></article>`).join('') || '<span class="muted">No active members yet.</span>';
  const invitations = (team.invitations || []).map(inv => `<article class="insight"><span class="kind">INVITED · ${esc(inv.role || 'event organizer')}</span><strong>${esc(inv.email)}</strong><small class="muted">Invitation pending</small></article>`).join('');
  return `<div id="team-member-list" class="insight-list">${members}${invitations}</div>`;
}
async function teamModal() {
  document.querySelector('.modal-backdrop')?.remove();
  const organizationId = state.data?.event?.organization_id || 'org-demo';
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal team-modal"><div class="eyebrow">Workspace access</div><h2>Team & roles</h2><p>Invite collaborators and keep event operations scoped to the right role.</p><div id="team-panel-content"><span class="muted">Loading workspace members…</span></div><div class="organizer-divider"></div><form id="invite-form" class="form-grid"><label>Invite by email<input type="email" name="email" required placeholder="teammate@company.com"></label><label>Role<select name="role"><option value="event_organizer">Event organizer</option><option value="content_editor">Content editor</option><option value="speaker">Speaker</option><option value="attendee">Attendee</option><option value="organization_admin">Organization admin</option></select></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Close</button><button class="btn lime">Send invitation ↗</button></div></form></div></div>`);
  try {
    const team = await api(`/api/team?organization_id=${encodeURIComponent(organizationId)}`);
    const panel = document.querySelector('#team-panel-content');
    if (panel) panel.innerHTML = teamPanelMarkup(team);
  } catch (error) { const panel = document.querySelector('#team-panel-content'); if (panel) panel.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
document.addEventListener('click', async e => { const nav=e.target.closest('.nav-item'); if(nav){state.view=nav.dataset.view;render();return} const viewLink=e.target.closest('[data-view-link]'); if(viewLink){e.preventDefault();const next=viewLink.dataset.viewLink;if(next==='auth'){if(state.authenticated){state.view='overview'}else{state.authMode=viewLink.dataset.authMode || 'login';state.view='auth'}}else{state.view=next}render();return} const action=e.target.closest('[data-action]')?.dataset.action; if(!action)return; if(action==='forgot-inline'){const box=document.querySelector('.forgot-inline');if(box){box.hidden=!box.hidden;e.target.textContent=box.hidden?'Forgot password?':'Hide password reset';}return} if(action==='send-reset'){notify('If the email exists, a reset link is on its way');return} if(action==='manage-event'){eventModal('edit');return} if(action==='event-new'){eventModal('create');return} if(action==='manage-team'){teamModal();return} if(action==='add-session'){modal();return} if(action==='close-modal'){document.querySelector('.modal-backdrop')?.remove();return} if(action==='copy-link'){await navigator.clipboard?.writeText(document.querySelector('#share-link')?.value || attendeePortalUrl());notify('Attendee link copied to clipboard');return} if(action==='copy-embed'){await navigator.clipboard?.writeText(`<iframe src="${attendeePortalUrl()}" title="Smart Event Manager attendee portal" loading="lazy"></iframe>`);notify('Embed code copied');return} if(action==='share-social'){notify('Social share card prepared — copy the event link to post it');return} if(action==='generate'){try{const source=(state.data?.insights||[]).map(item=>item.title+': '+item.body).join('\n') || 'No transcript is available yet. Explain how an event team should prepare useful post-event takeaways.';const result=await api('/api/ai/summarize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:source,targetLanguage:'English'})});notify(`Gemini generated a ${result.model} content brief`)}catch(error){notify(error.message.includes('GEMINI_API_KEY')?'Add GEMINI_API_KEY to the server environment first':error.message)}return} if(action==='match'){notify('Finding high-intent attendee matches');return} if(action==='export'){notify('Export prepared for download');return} if(action==='library'){state.view='content';render();notify('Signal library opened');return} if(action==='share'){state.view='connect';render();return} });
document.addEventListener('click', e => {
  if (!e.target.closest('[data-action="open-screen"]')) return;
  e.preventDefault();
  state.view = 'screen';
  render();
}, true);
document.addEventListener('submit', async e => { if(e.target.id==='session-form'){e.preventDefault(); const payload=Object.fromEntries(new FormData(e.target)); await api('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); document.querySelector('.modal-backdrop')?.remove(); state.data=await api('/api/dashboard'); state.view='sessions'; render(); notify('Session added to the program'); return;} if(e.target.id==='login-form'){e.preventDefault();state.authenticated=true;await openWorkspace('Welcome back - organizer workspace ready');return} if(e.target.id==='register-form'){e.preventDefault();const f=new FormData(e.target);if(f.get('password')!==f.get('confirm')){notify('Passwords do not match');return}state.authenticated=true;await openWorkspace('Workspace created - welcome to Smart Event Manager');return} if(e.target.id==='forgot-form'){e.preventDefault();notify('If the email exists, a reset link is on its way');return} });
// Auth is handled by the backend/Supabase Auth. The capture-phase listener
// keeps the existing UI and prevents the old demo-only submit handler below
// from marking a user authenticated before the server accepts the request.
document.addEventListener('click', async e => { const action=e.target.closest('[data-action="send-reset"]')?.dataset.action; if(action!=='send-reset')return; e.preventDefault(); e.stopImmediatePropagation(); const email=document.querySelector('input[name="resetEmail"]')?.value || document.querySelector('#login-form input[name="email"]')?.value; if(!email)return notify('Enter your email address first'); try{const result=await api('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})}); if(result.reset_token){state.resetToken=`${result.reset_token}`;state.authMode='reset';state.view='auth';render();notify('Demo recovery form ready — choose a new password');}else notify('If the email exists, a reset link is on its way')}catch(error){notify(error.message)} }, true);
document.addEventListener('submit', async e => { const form=e.target; if(!['login-form','register-form','forgot-form'].includes(form.id))return; e.preventDefault(); e.stopImmediatePropagation(); const fields=Object.fromEntries(new FormData(form)); try { if(form.id==='register-form' && fields.password!==fields.confirm) throw new Error('Passwords do not match'); const endpoint=form.id==='login-form'?'/api/auth/login':form.id==='register-form'?'/api/auth/register':'/api/auth/forgot-password'; const payload=form.id==='forgot-form'?{email:fields.email}:{email:fields.email,password:fields.password}; const result=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if(form.id==='forgot-form'){notify('If the email exists, a reset link is on its way');return} state.authenticated=true; localStorage.setItem('smart-event-session',JSON.stringify(result.session||{})); await openWorkspace(form.id==='login-form'?'Welcome back - organizer workspace ready':'Workspace created - welcome to Smart Event Manager'); } catch(error){notify(error.message)} }, true);
function sessionEditModal(session) { document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Program builder</div><h2>Edit session</h2><p>Changes are saved to the active event workspace.</p><form class="form-grid" id="session-edit-form"><input type="hidden" name="session_id" value="${esc(session.id)}"><label>Session title<input name="title" required value="${esc(session.title || '')}"></label><label>Track<select name="track"><option ${session.track==='Main stage'?'selected':''}>Main stage</option><option ${session.track==='Leadership'?'selected':''}>Leadership</option><option ${session.track==='Growth'?'selected':''}>Growth</option></select></label><label>Speaker<input name="speaker" value="${esc(session.speaker || '')}"></label><label>Room<input name="room" value="${esc(session.room || '')}"></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button><button class="btn lime">Save changes</button></div></form></div></div>`); }
document.querySelector('#mobileMenu').addEventListener('click',()=>document.querySelector('.sidebar').classList.toggle('open'));
document.addEventListener('change', async e => { const selector=e.target.closest('.transcript-toolbar select'); if(!selector)return; const language=(selector.value||'English').split(' ')[0]; if(language==='English')return notify('Original English transcript selected'); const lines=[...document.querySelectorAll('.transcript-card .transcript-line')]; const source=lines.map(item=>item.querySelector('p')?.textContent || '').filter(Boolean).join('\n'); const status=document.querySelector('#translation-status'); if(status)status.textContent='Translating…'; try{let result; const sessionId=activeSessionId(); let segments=lines.map(item=>item.dataset.segmentId).filter(Boolean); if(!segments.length){const rows=await api(`/api/transcript-segments?session_id=${encodeURIComponent(sessionId)}`); segments=rows.slice(-lines.length).map(row=>row.id);} if(segments.length){const saved=[]; for(const segmentId of segments){const line=lines.find(item=>item.dataset.segmentId===segmentId)?.querySelector('p')?.textContent || source; saved.push(await api(`/api/transcript-segments/${encodeURIComponent(segmentId)}/translate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:line,targetLanguage:language})}));} result={targetLanguage:language,output:saved.map(item=>item.output).join('\n'),mode:saved.some(item=>item.mode==='live')?'live':'fallback'};}else{result=await api('/api/ai/translate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:source,targetLanguage:language})});} let output=document.querySelector('.translation-output'); if(!output){document.querySelector('.transcript-card')?.insertAdjacentHTML('afterbegin','<div class="translation-output insight"></div>');output=document.querySelector('.translation-output')} output.innerHTML=`<span class="kind">${esc(result.targetLanguage)} translation</span><p>${esc(result.output)}</p>`; if(status)status.textContent=result.mode==='fallback'?'Local preview':'Saved'; }catch(error){if(status)status.textContent=error.message} });
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-question-moderate]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const payload = button.dataset.moderationPinned ? {pinned: button.dataset.moderationPinned === 'true'} : {status: button.dataset.moderationStatus};
  try {
    await api(`/api/questions/${encodeURIComponent(button.dataset.questionModerate)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    state.data = await api('/api/dashboard');
    render();
    notify('Question moderation saved');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="edit-session"], [data-action="duplicate-session"], [data-action="delete-session"], [data-action="start-session"], [data-action="stop-session"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const sessionId = button.dataset.sessionId;
  const action = button.dataset.action;
  const session = state.data?.sessions?.find(item => item.id === sessionId);
  if (!session) return notify('Session could not be found');
  try {
    if (action === 'start-session' || action === 'stop-session') {
      await api(`/api/sessions/${encodeURIComponent(sessionId)}/${action === 'start-session' ? 'start' : 'stop'}`, {method:'POST'});
      state.data = await api('/api/dashboard');
      render();
      notify(action === 'start-session' ? 'Session is live — capture is ready' : 'Session marked complete');
      return;
    }
    if (action === 'edit-session') { sessionEditModal(session); return; }
    if (action === 'duplicate-session') {
      await api(`/api/sessions/${encodeURIComponent(sessionId)}/duplicate`, {method:'POST'});
      state.data = await api('/api/dashboard');
      render();
      notify('Session duplicated');
      return;
    }
    if (!window.confirm(`Delete “${session.title}”? This removes the session from the agenda.`)) return;
    await api(`/api/sessions/${encodeURIComponent(sessionId)}`, {method:'DELETE'});
    state.data = await api('/api/dashboard');
    render();
    notify('Session deleted');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('submit', async event => {
  if (event.target.id !== 'session-edit-form') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(event.target));
  try {
    await api(`/api/sessions/${encodeURIComponent(fields.session_id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:fields.title, track:fields.track, speaker:fields.speaker, room:fields.room})});
    document.querySelector('.modal-backdrop')?.remove();
    state.data = await api('/api/dashboard');
    render();
    notify('Session updated');
  } catch (error) { notify(error.message); }
}, true);

// Keep public auth links reliable even when the landing page is loaded directly.
document.addEventListener('click', event => {
  const authTab = event.target.closest('[data-auth-tab]');
  if (authTab) {
    event.preventDefault();
    event.stopImmediatePropagation();
    state.authMode = authTab.dataset.authTab || 'login';
    state.view = 'auth';
    render();
    return;
  }
  const link = event.target.closest('[data-view-link="auth"]');
  if (!link || state.authenticated) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  state.authMode = link.dataset.authMode || 'login';
  state.view = 'auth';
  history.replaceState({view: 'auth'}, '', `${location.pathname}${location.search}#auth`);
  render();
}, true);

document.addEventListener('click', event => {
  const button = event.target.closest('.attendee-card .translation-row .btn');
  if (!button || state.view !== 'attendee') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  state.attendeeLanguage = document.querySelector('#language')?.value || 'English';
  state.view = 'transcript';
  render();
  const selector = document.querySelector('.transcript-toolbar select');
  if (selector && state.attendeeLanguage !== 'English') {
    const option = [...selector.options].find(item => item.value.startsWith(state.attendeeLanguage));
    if (option) { selector.value = option.value; selector.dispatchEvent(new Event('change', {bubbles:true})); }
  }
}, true);

document.addEventListener('click', event => {
  const button = event.target.closest('[data-action="edit-segment"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  document.querySelector('.modal-backdrop')?.remove();
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Transcript correction</div><h2>Edit transcript line</h2><p class="muted">Save a corrected version while preserving the original in revision history.</p><form id="transcript-edit-form" class="form-grid" data-segment-id="${esc(button.dataset.segmentId)}"><label>Transcript text<textarea name="text" rows="6" required>${esc(button.dataset.segmentText || '')}</textarea></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button><button class="btn lime">Save correction ↗</button></div></form></div></div>`);
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#transcript-edit-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  try {
    await api(`/api/transcript-segments/${encodeURIComponent(form.dataset.segmentId)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:new FormData(form).get('text'), editor:'organizer'})});
    document.querySelector('.modal-backdrop')?.remove();
    state.data = {...state.data, transcripts: await api(`/api/transcripts?session_id=${encodeURIComponent(activeSessionId())}`)};
    mountTranscriptData();
    notify('Transcript correction saved with revision history');
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="notifications"], [data-action="help"], [data-action="share-social"]');
  if (!button) return;
  const action = button.dataset.action;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (action === 'notifications') {
    document.querySelector('.modal-backdrop')?.remove();
    document.body.insertAdjacentHTML('beforeend', '<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Workspace activity</div><h2>Notifications</h2><div class="insight-list"><article class="insight"><span class="kind">LIVE</span><strong>Event intelligence is ready</strong><p>New transcript signals will appear in the dashboard as your session runs.</p></article><article class="insight"><span class="kind">CONTENT</span><strong>Reports stay evidence-linked</strong><p>Generated reports can be downloaded from Content Studio.</p></article></div><div class="modal-actions"><button type="button" class="btn lime" data-action="close-modal">Done</button></div></div></div>');
    return;
  }
  if (action === 'help') {
    document.querySelector('.modal-backdrop')?.remove();
    document.body.insertAdjacentHTML('beforeend', '<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Smart Event Manager</div><h2>Quick help</h2><p>Capture a live text or recording, review the transcript, generate grounded takeaways, then create reports or share the attendee portal.</p><div class="insight-list"><div class="insight"><span class="kind">LIVE</span><strong>Start with Capture</strong><p>Use browser audio, upload a file, or send a text chunk from an event bridge.</p></div><div class="insight"><span class="kind">REMIX</span><strong>Open Content Studio</strong><p>Generate versioned assets and export evidence-linked reports.</p></div></div><div class="modal-actions"><button type="button" class="btn lime" data-action="close-modal">Close</button></div></div></div>');
    return;
  }
  const eventName = state.data?.event?.name || 'Smart Event Manager event';
  const shareUrl = `${location.origin}/attendee/${state.data?.event?.slug || 'event'}`;
  const shareData = {title: eventName, text: `Join ${eventName} live`, url: shareUrl};
  try { if (navigator.share) await navigator.share(shareData); else { await navigator.clipboard?.writeText(shareUrl); notify('Event link copied — ready to share'); } } catch (_) { notify('Sharing was cancelled'); }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#report-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  const button = form.querySelector('button[type="submit"], button:not([type])');
  if (button) button.disabled = true;
  try {
    await api('/api/reports/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...fields, event_id:state.data?.event?.id || null})});
    document.querySelector('.report-studio')?.remove();
    await mountReportStudio();
    notify('Grounded event report generated');
  } catch (error) { notify(error.message); }
  finally { if (button) button.disabled = false; }
}, true);
document.addEventListener('change', async event => {
  const picker = event.target.closest('#attendee-session-select');
  if (!picker) return;
  state.currentSessionId = picker.value;
  try {
    state.data = {...state.data, transcripts: await api(`/api/transcripts?session_id=${encodeURIComponent(activeSessionId())}`)};
    render();
    notify('Viewing the selected session');
  } catch (error) { notify(error.message); }
}, true);
function showMatchResults(matches, attendee) {
  document.querySelector('.match-results')?.remove();
  const body = matches.length ? matches.map(match => `<div class="insight"><span class="kind">${match.match_score}% match</span><strong>${esc(match.attendee.full_name)} · ${esc(match.attendee.role || '')}</strong><p>${esc(match.reason)}</p><div class="actions-row">${match.shared_interests.map(interest => `<span class="badge">${esc(interest)}</span>`).join('')}</div></div>`).join('') : '<p class="muted">No overlapping interests found yet.</p>';
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop match-results"><div class="modal"><div class="eyebrow">Networking intelligence</div><h2>Best connections for ${esc(attendee.full_name)}</h2><p>Matches are ranked from shared interests and attendee intent signals.</p><div class="insight-list">${body}</div><div class="modal-actions"><button class="btn lime" data-action="close-match-results">Done</button></div></div></div>`);
}
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="match"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const attendee = state.data?.attendees?.[0];
  if (!attendee) return notify('Load attendee data before finding matches');
  try {
    const matches = await api(`/api/attendees/matches?attendee_id=${encodeURIComponent(attendee.id)}`);
    showMatchResults(matches, attendee);
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('click', event => {
  if (event.target.closest('[data-action="close-match-results"]')) event.target.closest('.match-results')?.remove();
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="check-in"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const attendeeId = button.dataset.attendeeId;
  const checkedIn = button.dataset.checked !== 'true';
  try {
    const updated = await api(`/api/attendees/${encodeURIComponent(attendeeId)}/check-in`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({checked_in:checkedIn})});
    state.data.attendees = state.data.attendees.map(attendee => attendee.id === attendeeId ? updated : attendee);
    render();
    notify(checkedIn ? 'Attendee checked in' : 'Check-in reversed');
  } catch (error) { notify(error.message); }
}, true);
load();
document.addEventListener('submit', async event => {
  const form = event.target;
  if (form.id !== 'reset-form') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  if (fields.password !== fields.confirm) return notify('Passwords do not match');
  if (!state.resetToken) return notify('This recovery link is missing or expired');
  try {
    await api('/api/auth/reset-password', {method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${state.resetToken}`}, body:JSON.stringify({password:fields.password})});
    state.resetToken = '';
    state.authMode = 'login';
    state.view = 'auth';
    history.replaceState({view:'auth'}, '', `${location.pathname}#auth`);
    render();
    notify('Password updated. You can now log in.');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('click', e => { const button=e.target.closest('[data-action="export"]'); if(!button)return; e.preventDefault(); e.stopImmediatePropagation(); const rows=state.view==='attendees'?state.data?.attendees:state.data?.sessions; downloadCsv(`${state.view}-export.csv`,rows); }, true);

// Capture is intentionally mounted as a workflow overlay so it remains usable
// even while the organizer dashboard is waiting for Supabase data.
function captureView() { return `${head('Live content engine', 'Capture the room', 'Connect an audio source, upload a recording, or paste a live text chunk. Every capture becomes searchable event intelligence.', '<span class="badge orange">● READY TO CAPTURE</span>')}
<div class="capture-grid"><section class="card capture-card"><div class="capture-card-top"><div><span class="capture-icon">◉</span><h2>Live capture</h2><p>Use your laptop microphone or mixer feed. This browser capture is ideal for a quick test; production audio can come from your venue mixer or meeting bot.</p></div><span class="capture-status" id="capture-status">Idle</span></div><div class="capture-controls"><button class="btn lime" data-capture-action="start">Start microphone</button><button class="btn ghost" data-capture-action="pause" disabled>Pause</button><button class="btn ghost" data-capture-action="resume" disabled>Resume</button><button class="btn ghost" data-capture-action="mute" disabled>Mute</button><button class="btn ghost" data-capture-action="stop" disabled>Stop</button></div><div class="capture-meter"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div><small class="muted">Use headphones and ask the speaker for consent before capturing.</small></section>
<section class="card capture-card"><div class="capture-card-top"><div><span class="capture-icon">↥</span><h2>Upload a recording</h2><p>Upload audio, video, or an existing TXT/SRT/VTT transcript. The file is validated, queued, and processed as a background job.</p></div></div><form id="upload-form" class="upload-form"><label class="upload-drop"><input type="file" name="file" accept="audio/*,video/mp4,video/quicktime,text/plain,.srt,.vtt" required><strong>Choose a recording or transcript</strong><span>Up to 20 MB · queued processing with status tracking</span></label><select name="language"><option value="auto">Detect language</option><option>English</option><option>Tamil</option><option>Spanish</option><option>French</option><option>Japanese</option></select><button class="btn purple">Queue transcription ↗</button></form><div id="upload-status" class="muted" aria-live="polite"></div></section></div>
<section class="card section-card capture-text-card"><div class="card-head"><div><h2>Live text bridge</h2><p class="muted">Use this to test the end-to-end flow before connecting a venue mixer or meeting integration.</p></div><span class="badge">No app required for attendees</span></div><form id="capture-form" class="capture-form"><textarea name="text" rows="4" required placeholder="Paste a short live excerpt, for example: Our biggest opportunity is making the next action obvious for every attendee."></textarea><div class="capture-form-row"><input name="speaker" placeholder="Speaker name (optional)" value="Live speaker"><select name="language"><option value="auto">Auto language</option><option>English</option><option>Tamil</option><option>Spanish</option><option>French</option></select><button class="btn lime">Add live capture</button></div></form></section>`; }

document.addEventListener('click', async e => {
  const button = e.target.closest('.capture-launch');
  if (button) {
    e.preventDefault();
    history.pushState({view: 'capture'}, '', `${location.pathname}${location.search}#capture`);
    e.stopImmediatePropagation();
    app.innerHTML = captureView();
    document.body.classList.remove('marketing');
    document.body.classList.add('workspace-view');
    document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item === button));
    return;
  }
  const captureAction = e.target.closest('[data-capture-action]')?.dataset.captureAction;
  if (!captureAction) return;
  e.preventDefault();
  if (captureAction === 'start') {
    if (!navigator.mediaDevices?.getUserMedia) return notify('Microphone capture is unavailable in this browser');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__captureStream = stream;
      if (!window.MediaRecorder) return notify('Audio recording is unavailable in this browser');
      const chunks = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = async () => {
        const status = document.querySelector('#capture-status');
        if (!chunks.length) return notify('No audio was captured');
        if (status) status.textContent = 'Transcribing';
        try {
          const form = new FormData();
          form.append('file', new Blob(chunks, {type: recorder.mimeType || 'audio/webm'}), 'live-capture.webm');
          form.append('session_id', activeSessionId());
          form.append('language', 'auto');
          const result = await api('/api/transcription/batch', {method:'POST', body:form});
          state.data = await api('/api/dashboard');
          if (status) status.textContent = 'Transcribed';
          notify(`Audio transcribed with ${result.model || 'the configured model'}`);
        } catch (error) {
          if (status) status.textContent = 'Capture failed';
          notify(error.message);
        }
      };
      window.__captureRecorder = recorder;
      recorder.start(1000);
      await api(`/api/sessions/${encodeURIComponent(activeSessionId())}/start`, {method:'POST'}).catch(() => null);
      document.querySelector('#capture-status').textContent = 'Listening';
      document.querySelector('[data-capture-action="start"]').disabled = true;
      document.querySelector('[data-capture-action="pause"]').disabled = false;
      document.querySelector('[data-capture-action="resume"]').disabled = true;
      document.querySelector('[data-capture-action="mute"]').disabled = false;
      document.querySelector('[data-capture-action="stop"]').disabled = false;
      notify('Microphone connected — recording live audio');
    } catch (_) { notify('Microphone permission was not granted'); }
  } else if (captureAction === 'pause') {
    const recorder = window.__captureRecorder;
    if (recorder?.state === 'recording') {
      recorder.pause();
      document.querySelector('#capture-status').textContent = 'Paused';
      document.querySelector('[data-capture-action="pause"]').disabled = true;
      document.querySelector('[data-capture-action="resume"]').disabled = false;
      notify('Capture paused');
    }
  } else if (captureAction === 'resume') {
    const recorder = window.__captureRecorder;
    if (recorder?.state === 'paused') {
      recorder.resume();
      document.querySelector('#capture-status').textContent = 'Listening';
      document.querySelector('[data-capture-action="pause"]').disabled = false;
      document.querySelector('[data-capture-action="resume"]').disabled = true;
      notify('Capture resumed');
    }
  } else if (captureAction === 'mute') {
    const tracks = window.__captureStream?.getAudioTracks() || [];
    if (!tracks.length) return notify('Start microphone capture first');
    const muted = tracks.some(track => track.enabled);
    tracks.forEach(track => { track.enabled = !muted; });
    const button = document.querySelector('[data-capture-action="mute"]');
    if (button) button.textContent = muted ? 'Unmute' : 'Mute';
    document.querySelector('#capture-status').textContent = muted ? 'Muted' : 'Listening';
    notify(muted ? 'Microphone muted' : 'Microphone unmuted');
  } else if (captureAction === 'stop') {
    if (window.__captureRecorder?.state === 'recording') window.__captureRecorder.stop();
    window.__captureStream?.getTracks().forEach(track => track.stop());
    await api(`/api/sessions/${encodeURIComponent(activeSessionId())}/stop`, {method:'POST'}).catch(() => null);
    document.querySelector('#capture-status').textContent = 'Processing';
    document.querySelector('[data-capture-action="start"]').disabled = false;
    document.querySelector('[data-capture-action="pause"]').disabled = true;
    document.querySelector('[data-capture-action="resume"]').disabled = true;
    document.querySelector('[data-capture-action="mute"]').disabled = true;
    document.querySelector('[data-capture-action="stop"]').disabled = true;
    notify('Microphone stopped — processing the recording');
  }
}, true);

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-event-switch]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  try {
    state.data = await api(`/api/dashboard?event_id=${encodeURIComponent(button.dataset.eventSwitch)}`);
    state.currentSessionId = activeSessionId();
    document.querySelector('.modal-backdrop')?.remove();
    state.view = 'overview';
    render();
    notify(`Switched to ${state.data.event?.name || 'event workspace'}`);
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#event-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  const payload = {
    organization_id: state.data?.event?.organization_id || null,
    name: fields.name,
    slug: fields.slug,
    venue: fields.venue,
    status: fields.status,
    starts_at: fields.starts_at ? new Date(fields.starts_at).toISOString() : new Date().toISOString(),
    ends_at: fields.ends_at ? new Date(fields.ends_at).toISOString() : new Date().toISOString(),
    brand_color: state.data?.event?.brand_color || '#7568f3'
  };
  try {
    const result = fields.mode === 'create'
      ? await api('/api/events', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      : await api(`/api/events/${encodeURIComponent(fields.event_id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const dashboard = await api('/api/dashboard');
    if (fields.mode === 'create') {
      state.data = {...dashboard, event: result, sessions: [], insights: [], transcripts: [], share_links: await api(`/api/share-links?event_id=${encodeURIComponent(result.id)}`)};
      state.currentSessionId = '';
    } else {
      state.data = {...dashboard, event: result};
    }
    document.querySelector('.modal-backdrop')?.remove();
    state.view = 'overview';
    render();
    notify(fields.mode === 'create' ? 'New event workspace created' : 'Event details saved');
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#invite-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  try {
    await api('/api/team/invitations', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:fields.email, role:fields.role, organization_id:state.data?.event?.organization_id || 'org-demo'})});
    notify('Invitation created');
    await teamModal();
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('change', async event => {
  const select = event.target.closest('[data-team-role]');
  if (!select) return;
  try {
    await api(`/api/team/members/${encodeURIComponent(select.dataset.teamRole)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:select.value})});
    notify('Team role updated');
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-poll-toggle]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  button.disabled = true;
  try {
    await api(`/api/polls/${encodeURIComponent(button.dataset.pollToggle)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({is_open: button.dataset.pollOpen === 'true'})});
    state.data = await api('/api/dashboard');
    render();
    notify(button.dataset.pollOpen === 'true' ? 'Poll is now live for attendees' : 'Poll closed');
  } catch (error) { button.disabled = false; notify(error.message); }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#poll-create-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  const options = [fields.option_one, fields.option_two, fields.option_three].map(value => String(value || '').trim()).filter(Boolean);
  try {
    await api('/api/polls', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:fields.question, poll_type:fields.poll_type, options, session_id:activeSessionId()})});
    state.data = await api('/api/dashboard');
    render();
    notify('Poll published — open it when the room is ready');
  } catch (error) { notify(error.message); }
}, true);

function mountAttendeeSessionPicker() {
  if (state.view !== 'attendee' || document.querySelector('#attendee-session-select')) return;
  const sessions = state.data?.sessions || [];
  if (!sessions.length) return;
  const options = sessions.map(session => `<option value="${esc(session.id)}" ${session.id === activeSessionId() ? 'selected' : ''}>${esc(session.title)} · ${esc(session.track || '')}</option>`).join('');
  document.querySelector('.attendee-card')?.insertAdjacentHTML('beforeend', `<label class="session-picker">Session<select id="attendee-session-select">${options}</select></label>`);
}
function mountAttendeeInteractions() {
  if (document.querySelector('.attendee-interactions')) return;
  document.querySelector('.attendee-view')?.insertAdjacentHTML('beforeend', `<section class="attendee-interactions"><div class="attendee-interaction-card"><div class="eyebrow">Audience voice</div><h2>Ask the room</h2><p>Share a question with the organizer. You can stay anonymous.</p><form id="question-form"><textarea name="body" rows="3" required placeholder="What would you like the speaker to answer?"></textarea><label class="check-row"><input type="checkbox" name="anonymous" checked> Ask anonymously</label><button class="btn lime">Submit question ↗</button></form><div id="question-status" class="muted"></div></div><div class="attendee-interaction-card"><div class="eyebrow">Close the loop</div><h2>Rate this session</h2><p>Your feedback helps the event team improve the next room.</p><form id="feedback-form"><div class="rating-row"><label>Speaker<select name="speaker_rating"><option value="">—</option><option value="5">5 · Excellent</option><option value="4">4 · Good</option><option value="3">3 · Okay</option><option value="2">2 · Needs work</option><option value="1">1 · Poor</option></select></label><label>Content<select name="content_rating"><option value="">—</option><option value="5">5 · Excellent</option><option value="4">4 · Good</option><option value="3">3 · Okay</option><option value="2">2 · Needs work</option><option value="1">1 · Poor</option></select></label></div><textarea name="comment" rows="3" placeholder="Optional feedback"></textarea><button class="btn purple">Send feedback ↗</button></form></div></section>`);
}
function mountTranscriptData() {
  if (state.view !== 'transcript') return;
  const rows = state.data?.transcripts || [];
  if (!rows.length) return;
  const card = document.querySelector('.transcript-card');
  if (!card) return;
  card.innerHTML = rows.slice(0, 24).reverse().map((row, index) => `<div class="transcript-line ${index === rows.length - 1 ? 'active' : ''}" data-segment-id="${esc(row.segment_id || '')}"><span>${row.created_at ? new Date(row.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : 'LIVE'}</span><div><p>${esc(row.text || '')}</p><small class="muted">${esc(row.speaker || 'Live speaker')} · ${esc(row.language || 'auto')}</small>${row.segment_id ? `<button class="btn ghost transcript-edit-button" data-action="edit-segment" data-segment-id="${esc(row.segment_id)}" data-segment-text="${esc(row.text || '')}">Edit line</button>` : ''}</div></div>`).join('');
}
async function refreshLiveTranscript() {
  if (state.view !== 'transcript') return;
  try {
    const rows = await api(`/api/transcripts?session_id=${encodeURIComponent(activeSessionId())}`);
    state.data = {...state.data, transcripts: rows};
    mountTranscriptData();
    const status = document.querySelector('.live-sync-status');
    if (status) status.textContent = `Live sync - ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`;
  } catch (_) {
    const status = document.querySelector('.live-sync-status');
    if (status) status.textContent = 'Live sync paused';
  }
}
function mountLiveTranscriptSync() {
  clearInterval(transcriptRefreshTimer);
  transcriptRefreshTimer = null;
  if (state.view !== 'transcript') return;
  document.querySelector('.transcript-toolbar')?.insertAdjacentHTML('beforeend', document.querySelector('.live-sync-status') ? '' : '<span class="live-sync-status muted">Live sync on</span>');
  refreshLiveTranscript();
  transcriptRefreshTimer = setInterval(refreshLiveTranscript, 5000);
}
async function mountOrganizerAudience() {
  if (state.view !== 'attendees' || document.querySelector('.organizer-audience-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card organizer-audience-panel"><div class="card-head"><div><h2>Audience questions</h2><p class="muted">Review incoming questions, pin the best ones, and move them through the event team workflow.</p></div><span class="badge">Moderator queue</span></div><div id="organizer-question-list" class="insight-list"><span class="muted">Loading questions…</span></div><div class="organizer-divider"></div><div class="card-head"><div><h2>Live poll studio</h2><p class="muted">Publish a focused question to the attendee portal, then open or close it as the conversation moves.</p></div><span class="badge">Audience pulse</span></div><form id="poll-create-form" class="capture-form"><label>Question<input name="question" required minlength="3" placeholder="What should the room explore next?"></label><div class="capture-form-row"><label>Response type<select name="poll_type"><option value="single">Single choice</option><option value="multiple">Multiple choice</option><option value="yes_no">Yes / No</option></select></label><label>Option 1<input name="option_one" required placeholder="First option"></label><label>Option 2<input name="option_two" required placeholder="Second option"></label><label>Option 3<input name="option_three" placeholder="Optional third option"></label><button class="btn lime">Publish poll ↗</button></div></form><div id="organizer-poll-list" class="insight-list"><span class="muted">Loading polls…</span></div></section>');
  try {
    const [questions, polls] = await Promise.all([api(`/api/questions?session_id=${encodeURIComponent(activeSessionId())}`), api(`/api/polls?session_id=${encodeURIComponent(activeSessionId())}`)]);
    const list = document.querySelector('#organizer-question-list');
    if (list) list.innerHTML = questions.length ? questions.map(question => `<article class="insight"><span class="kind">${esc(question.status || 'pending')} · ${question.votes || 0} votes ${question.pinned ? '· pinned' : ''}</span><strong>${esc(question.body)}</strong><div class="actions-row compact-actions"><button class="btn lime" data-question-moderate="${esc(question.id)}" data-moderation-status="approved">Approve</button><button class="btn ghost" data-question-moderate="${esc(question.id)}" data-moderation-status="answered">Mark answered</button><button class="btn ghost" data-question-moderate="${esc(question.id)}" data-moderation-pinned="${question.pinned ? 'false' : 'true'}">${question.pinned ? 'Unpin' : 'Pin'}</button><button class="btn ghost" data-question-moderate="${esc(question.id)}" data-moderation-status="dismissed">Dismiss</button></div></article>`).join('') : '<span class="muted">No questions are waiting for review.</span>';
    const pollList = document.querySelector('#organizer-poll-list');
    if (pollList) pollList.innerHTML = polls.length ? polls.map(poll => `<article class="insight"><span class="kind">${poll.is_open ? 'OPEN' : 'CLOSED'} · ${esc(poll.poll_type || 'single')} · ${poll.results?.total_responses || 0} responses</span><strong>${esc(poll.question)}</strong><small class="muted">${(poll.options || []).map(option => `${esc(option.label)} (${option.responses || 0})`).join(' · ') || 'Free response'}</small><div class="actions-row compact-actions"><button class="btn ${poll.is_open ? 'ghost' : 'lime'}" data-poll-toggle="${esc(poll.id)}" data-poll-open="${poll.is_open ? 'false' : 'true'}">${poll.is_open ? 'Close poll' : 'Open for attendees'}</button></div></article>`).join('') : '<span class="muted">No polls yet. Publish the first audience pulse above.</span>';
  } catch (error) { const list = document.querySelector('#organizer-question-list'); if (list) list.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountAssetLibrary() {
  if (state.view !== 'content' || document.querySelector('.asset-library')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card asset-library"><div class="card-head"><div><h2>Saved content library</h2><p class="muted">Generated briefs, recaps, and speaker packs remain attached to this event.</p></div><span class="badge">Persistent assets</span></div><div id="asset-list" class="insight-list"><span class="muted">Loading generated assets…</span></div></section>');
  try {
    const assets = await api(`/api/content/assets?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    state.assets = assets;
    const list = document.querySelector('#asset-list');
    if (list) list.innerHTML = assets.length ? assets.map(asset => { const output = typeof asset.content === 'object' ? asset.content.output || '' : asset.content || ''; const evidenceCount = typeof asset.content === 'object' ? (asset.content.evidence || []).length : 0; return `<article class="insight asset-item"><span class="kind">${esc(asset.asset_type || 'content')} · ${esc(asset.status || 'draft')} · ${evidenceCount} sources</span><strong>${esc(asset.title || 'Generated asset')}</strong><small class="muted">${asset.created_at ? date(asset.created_at) : 'Saved now'}</small><details><summary>View content</summary><pre>${esc(output)}</pre></details><div class="actions-row compact-actions"><button class="btn ghost" data-action="copy-asset" data-asset-id="${esc(asset.id)}">Copy content</button><button class="btn ghost" data-action="export-asset" data-format="markdown" data-asset-id="${esc(asset.id)}">↓ Markdown</button><button class="btn ghost" data-action="export-asset" data-format="json" data-asset-id="${esc(asset.id)}">↓ JSON</button><button class="btn ghost" data-action="edit-asset" data-asset-id="${esc(asset.id)}">Edit version</button></div></article>`; }).join('') : '<span class="muted">No generated assets yet. Create a brief or recap above.</span>';
  } catch (error) { const list = document.querySelector('#asset-list'); if (list) list.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountReportStudio() {
  if (state.view !== 'content' || document.querySelector('.report-studio')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card report-studio"><div class="card-head"><div><h2>Strategic reports</h2><p class="muted">Create an evidence-linked brief from the active event, then export it as Markdown or JSON.</p></div><span class="badge">Grounded output</span></div><form id="report-form" class="form-grid"><div class="capture-form-row"><label>Report type<select name="report_type"><option value="executive_brief">Executive brief</option><option value="full_event_summary">Full event summary</option><option value="sponsor_report">Sponsor report</option><option value="topic_analysis">Topic analysis</option><option value="engagement_report">Engagement report</option></select></label><label>Format<select name="format"><option value="markdown">Markdown</option><option value="json">JSON</option></select></label></div><label>Title<input name="title" value="Event intelligence report" required></label><button class="btn lime">Generate report ↗</button></form><div id="report-list" class="insight-list"><span class="muted">Loading reports…</span></div></section>');
  try {
    const reports = await api(`/api/reports?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    const list = document.querySelector('#report-list');
    if (list) list.innerHTML = reports.length ? reports.map(report => `<article class="insight"><span class="kind">${esc(report.report_type)} · ${esc(report.status)}</span><strong>${esc(report.title)}</strong><small class="muted">${report.source_session_ids?.length || 0} source sessions · ${report.created_at ? date(report.created_at) : 'Saved now'}</small><div class="actions-row compact-actions"><button class="btn ghost" data-action="export-report" data-report-id="${esc(report.id)}" data-format="markdown">Download Markdown</button><button class="btn ghost" data-action="export-report" data-report-id="${esc(report.id)}" data-format="json">View JSON</button></div></article>`).join('') : '<span class="muted">No reports yet. Generate the first event brief above.</span>';
  } catch (error) { const list = document.querySelector('#report-list'); if (list) list.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountTakeawayPanel() {
  if (state.view !== 'content' || document.querySelector('.takeaway-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card takeaway-panel"><div class="card-head"><div><h2>Evidence-linked takeaways</h2><p class="muted">Turn captured signals into reviewable decisions with source evidence attached.</p></div><button class="btn lime" data-action="generate-takeaways">Generate takeaway ↗</button></div><div id="takeaway-list" class="insight-list"><span class="muted">Loading saved takeaways…</span></div></section>');
  try {
    const rows = await api(`/api/takeaways?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    const list = document.querySelector('#takeaway-list');
    if (list) list.innerHTML = rows.length ? rows.map(row => `<article class="insight"><span class="kind">${esc(row.confidence ? `${Math.round(row.confidence * 100)}% confidence` : 'TAKEAWAY')} · ${esc(row.session_id || 'event')}</span><strong>${esc(row.title || 'Event takeaway')}</strong><p>${esc(row.body || '')}</p><small class="muted">${(row.evidence || []).length} linked source${(row.evidence || []).length === 1 ? '' : 's'}</small></article>`).join('') : '<span class="muted">No takeaways saved yet. Generate one after capturing a session.</span>';
  } catch (error) { const list = document.querySelector('#takeaway-list'); if (list) list.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountBrandKit() {
  if (state.view !== 'content' || document.querySelector('.brand-kit-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card brand-kit-panel"><div class="card-head"><div><h2>Brand kit</h2><p class="muted">Keep generated assets consistent with your organization identity.</p></div><span class="badge">Content defaults</span></div><form id="brand-kit-form" class="form-grid"><div class="capture-form-row"><label>Brand name<input name="name" placeholder="Organization name"></label><label>Website<input name="website" type="url" placeholder="https://example.com"></label></div><div class="capture-form-row"><label>Primary<input name="primary_color" type="text" placeholder="#7568f3"></label><label>Secondary<input name="secondary_color" type="text" placeholder="#1b1c2d"></label><label>Accent<input name="accent_color" type="text" placeholder="#e4ff63"></label></div><label>Tone<input name="tone" placeholder="clear, generous, modern"></label><div class="modal-actions"><button class="btn lime">Save brand kit ↗</button></div></form></section>');
  try {
    const kit = await api(`/api/brand-kit?organization_id=${encodeURIComponent(state.data?.event?.organization_id || 'org-demo')}`);
    state.brandKit = kit;
    if (kit.primary_color) document.documentElement.style.setProperty('--purple', kit.primary_color);
    if (kit.secondary_color) document.documentElement.style.setProperty('--ink', kit.secondary_color);
    if (kit.accent_color) document.documentElement.style.setProperty('--lime', kit.accent_color);
    const form = document.querySelector('#brand-kit-form');
    if (form) Object.entries(kit).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value || ''; });
  } catch (error) { const form = document.querySelector('#brand-kit-form'); if (form) form.insertAdjacentHTML('afterend', `<span class="muted">${esc(error.message)}</span>`); }
}
async function mountAnalystPanel() {
  if (state.view !== 'content' || document.querySelector('.analyst-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card analyst-panel"><div class="card-head"><div><h2>Ask the event analyst</h2><p class="muted">Ask a question about captured sessions. Answers stay grounded in your event evidence.</p></div><span class="badge">Source linked</span></div><form id="analyst-form" class="capture-form"><textarea name="question" rows="3" required placeholder="What themes or opportunities appeared across the event?"></textarea><div class="capture-form-row"><button class="btn purple">Ask analyst ↗</button></div></form><div id="analyst-result" class="insight-list"></div><div class="analyst-history"><div class="eyebrow">Conversation history</div><div id="analyst-conversations" class="insight-list"><span class="muted">Loading analyst history…</span></div></div></section>');
  try {
    const conversations = await api(`/api/analyst/conversations?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    const history = document.querySelector('#analyst-conversations');
    if (history) history.innerHTML = conversations.length ? conversations.slice(0, 8).map(item => `<button class="insight analyst-conversation" data-analyst-conversation="${esc(item.id)}"><strong>${esc(item.title || 'Event analyst conversation')}</strong><small class="muted">${esc(item.updated_at || item.created_at || '')}</small></button>`).join('') : '<span class="muted">Your grounded questions will appear here.</span>';
  } catch (error) { const history = document.querySelector('#analyst-conversations'); if (history) history.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
function mountSearchPanel() {
  if (state.view !== 'content' || document.querySelector('.search-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card search-panel"><div class="card-head"><div><h2>Search event knowledge</h2><p class="muted">Find sessions, transcript passages, insights, and generated assets.</p></div><span class="badge">Global search</span></div><form id="search-form" class="capture-form"><div class="capture-form-row"><input name="query" minlength="2" required placeholder="Search sustainability, resilience, or a speaker name"><button class="btn ghost">Search ↗</button></div></form><div id="search-results" class="insight-list"></div></section>');
}
async function mountTopicCloud() {
  if (state.view !== 'attendee' || document.querySelector('.topic-cloud-panel')) return;
  document.querySelector('.attendee-view')?.insertAdjacentHTML('beforeend', '<section class="attendee-interactions topic-cloud-panel"><div class="attendee-interaction-card"><div class="eyebrow">Event intelligence</div><h2>Explore the idea cloud</h2><p>Tap a topic to see the captured evidence behind it.</p><div id="topic-cloud" class="actions-row"><span class="muted">Loading topics…</span></div><div id="topic-evidence" class="insight-list"></div></div></section>');
  try {
    const topics = await api(`/api/topics?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    const cloud = document.querySelector('#topic-cloud');
    if (cloud) cloud.innerHTML = topics.length ? topics.map(topic => `<button class="btn ghost" data-topic-label="${esc(topic.label)}" style="font-size:${Math.round(11 + topic.weight * 9)}px">${esc(topic.label)} <small>${topic.count}</small></button>`).join('') : '<span class="muted">Topic signals will appear after the first capture.</span>';
  } catch (error) { const cloud = document.querySelector('#topic-cloud'); if (cloud) cloud.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountAudienceData() {
  if (state.view !== 'attendee' || document.querySelector('.audience-data-panel')) return;
  document.querySelector('.attendee-view')?.insertAdjacentHTML('beforeend', '<section class="attendee-interactions audience-data-panel"><div class="attendee-interaction-card"><div class="eyebrow">Audience voice</div><h2>Questions and polls</h2><div id="question-feed" class="insight-list"><span class="muted">Loading audience activity…</span></div><div id="poll-feed" class="insight-list"></div></div></section>');
  try {
    const sessionId = encodeURIComponent(activeSessionId());
    const [questions, polls] = await Promise.all([api(`/api/questions?session_id=${sessionId}`), api(`/api/polls?session_id=${sessionId}`)]);
    const questionFeed = document.querySelector('#question-feed');
    if (questionFeed) questionFeed.innerHTML = questions.length ? questions.map(question => `<div class="insight"><span class="kind">${question.status || 'pending'} · ${question.votes || 0} votes</span><p>${esc(question.body)}</p><button class="btn ghost" data-question-vote="${esc(question.id)}">Upvote</button></div>`).join('') : '<span class="muted">No audience questions yet.</span>';
    const pollFeed = document.querySelector('#poll-feed');
    if (pollFeed) pollFeed.innerHTML = polls.length ? polls.map(poll => { const options = (poll.options || []).map(option => `<label class="poll-option"><input type="${poll.poll_type === 'multiple' ? 'checkbox' : 'radio'}" name="${poll.poll_type === 'multiple' ? 'option_ids' : 'option_id'}" value="${esc(option.id)}" ${poll.poll_type === 'multiple' ? '' : 'required'}> ${esc(option.label)}</label>`).join(''); const response = poll.is_open ? `<form class="poll-response-form" data-poll-id="${esc(poll.id)}">${options || '<input name="answer_text" placeholder="Your response" required>'}<button class="btn lime">Respond ↗</button></form>` : '<span class="muted">Poll closed · results are retained by the event team.</span>'; const results = poll.results?.total_responses ? `<small class="muted">${poll.results.total_responses} response${poll.results.total_responses === 1 ? '' : 's'} · ${(poll.options || []).map(option => `${esc(option.label)}: ${option.responses || 0}`).join(' · ')}</small>` : ''; return `<div class="insight"><span class="kind">${poll.is_open ? 'Live poll' : 'Closed poll'} · ${esc(poll.poll_type || 'single')}</span><strong>${esc(poll.question)}</strong>${response}${results}</div>`; }).join('') : '';
  } catch (error) { const feed = document.querySelector('#question-feed'); if (feed) feed.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
function mountAttendeeTabs() {
  if (state.view !== 'attendee' || document.querySelector('.attendee-tabs')) return;
  document.querySelector('.attendee-nav')?.insertAdjacentHTML('afterend', '<nav class="attendee-tabs" aria-label="Attendee portal sections"><button class="active" data-attendee-anchor="attendee-hero">Live</button><button data-attendee-anchor="attendee-summary-panel">Summary</button><button data-attendee-anchor="topic-cloud-panel">Idea cloud</button><button data-attendee-anchor="audience-data-panel">Q&A + Polls</button><button data-attendee-anchor="attendee-interactions">Feedback</button></nav>');
}
async function mountAttendeeSummary() {
  if (state.view !== 'attendee' || document.querySelector('.attendee-summary-panel')) return;
  document.querySelector('.attendee-view')?.insertAdjacentHTML('beforeend', '<section class="attendee-interactions attendee-summary-panel"><div class="attendee-interaction-card"><div class="eyebrow">Session intelligence</div><h2>Session summary</h2><p>Generate a grounded recap from the latest captured signals.</p><button class="btn lime" data-action="attendee-summary">Generate summary ↗</button><div id="attendee-summary-output" class="insight-list"></div></div></section>');
  try {
    const saved = await api(`/api/summaries?session_id=${encodeURIComponent(activeSessionId())}`);
    const latest = saved?.[0];
    const content = latest?.content || {};
    const output = document.querySelector('#attendee-summary-output');
    if (output && content.output) output.innerHTML = `<div class="insight"><span class="kind">Saved ${esc(latest.language || 'English')} summary</span><p>${esc(content.output)}</p><small class="muted">${content.evidence?.length || 0} linked transcript source${content.evidence?.length === 1 ? '' : 's'}</small></div>`;
  } catch (_) {
    // The summary panel remains usable when the public portal is opened before
    // the database has been migrated or before the first organizer capture.
  }
}
function mountTranscriptActions() {
  if (state.view !== 'transcript' || document.querySelector('[data-action="export-transcript"]')) return;
  document.querySelector('.transcript-view .actions')?.insertAdjacentHTML('afterbegin', '<button class="btn ghost" data-action="export-transcript" data-format="txt">↓ TXT</button><button class="btn ghost" data-action="export-transcript" data-format="srt">↓ SRT</button><button class="btn ghost" data-action="export-transcript" data-format="vtt">↓ VTT</button>');
}
function mountLiveMetrics() {
  if (state.view !== 'overview' || !state.data?.analytics) return;
  const cards = [...document.querySelectorAll('.kpis .kpi strong')];
  const stats = state.data.analytics;
  const live = state.data.sessions.find(session => session.status === 'live');
  [stats.attendees, live?.attendance || 0, stats.transcripts + stats.questions, stats.average_session_rating ? `${Math.round(stats.average_session_rating * 20)}%` : '—'].forEach((value, index) => { if (cards[index]) cards[index].textContent = typeof value === 'number' ? money(value) : value; });
}
async function mountEventIntelligence() {
  if (state.view !== 'overview' || document.querySelector('.event-intelligence-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card event-intelligence-panel"><div class="card-head"><div><div class="eyebrow">Cross-session intelligence</div><h2>What the whole event is saying</h2><p class="muted">Grounded themes, evidence, and relationships across every session.</p></div><span class="badge">Evidence linked</span></div><div id="event-intelligence-content" class="grid insight-grid"><span class="muted">Reading the event knowledge layer…</span></div></section>');
  try {
    const intelligence = await api(`/api/intelligence?event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    const themes = (intelligence.themes || []).slice(0, 8).map(topic => `<span class="badge">${esc(topic.label)} · ${topic.count}</span>`).join(' ') || '<span class="muted">Capture more sessions to reveal recurring themes.</span>';
    const relationships = (intelligence.cross_session_themes || []).slice(0, 4).map(item => `<article class="insight"><span class="kind">${esc(item.topic)} · ${item.frequency} signals</span><strong>${item.sessions.map(session => esc(session.title)).join(' ↔ ')}</strong><small class="muted">${item.evidence?.[0]?.snippet ? esc(item.evidence[0].snippet) : 'Evidence will appear as sessions are captured.'}</small></article>`).join('') || '<span class="muted">Cross-session relationships will appear after shared themes are captured.</span>';
    const target = document.querySelector('#event-intelligence-content');
    if (target) target.innerHTML = `<div><div class="eyebrow">Top themes</div><div class="actions-row">${themes}</div></div><div><div class="eyebrow">Connections across rooms</div><div class="insight-list">${relationships}</div></div>`;
  } catch (error) { const target = document.querySelector('#event-intelligence-content'); if (target) target.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
async function mountSessionShareLinks() {
  if (state.view !== 'connect' || document.querySelector('.session-share-panel')) return;
  const sessions = state.data?.sessions || [];
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card session-share-panel"><div class="card-head"><div><h2>Session QR links</h2><p class="muted">Create a focused attendee doorway for each room. Scanning it opens the matching session.</p></div><span class="badge">Session-aware</span></div><div id="session-share-list" class="insight-list"><span class="muted">Preparing session links…</span></div></section>');
  try {
    const rows = await Promise.all(sessions.map(async session => ({session, link: (await api(`/api/share-links?event_id=${encodeURIComponent(state.data?.event?.id || '')}&session_id=${encodeURIComponent(session.id)}`))[0]})));
    const list = document.querySelector('#session-share-list');
    if (list) list.innerHTML = rows.map(({session, link}) => { const url = `${location.origin}/?share=${encodeURIComponent(link.token)}#attendee`; const qr = `https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=${encodeURIComponent(url)}`; return `<article class="insight session-share-row"><div><span class="kind">${esc(session.track || 'Session')} · ${esc(session.room || '')}</span><strong>${esc(session.title)}</strong><div class="link-row"><input readonly value="${esc(url)}"><button class="btn ghost" data-session-share="${esc(url)}">Copy</button></div></div><img class="qr-image small-qr" src="${qr}" alt="QR code for ${esc(session.title)}"></article>`; }).join('') || '<span class="muted">Create a session to generate a session QR link.</span>';
  } catch (error) { const list = document.querySelector('#session-share-list'); if (list) list.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
const baseRender = render;
render = function wrappedRender() { baseRender(); syncEventChrome(); mountLiveMetrics(); mountEventIntelligence(); if (state.view === 'attendee') { mountAttendeeSessionPicker(); mountAttendeeInteractions(); } mountTranscriptData(); mountLiveTranscriptSync(); mountAssetLibrary(); mountReportStudio(); mountTakeawayPanel(); mountBrandKit(); mountOrganizerAudience(); mountAnalystPanel(); mountSearchPanel(); mountTopicCloud(); mountAudienceData(); mountAttendeeTabs(); mountAttendeeSummary(); mountTranscriptActions(); mountSessionShareLinks(); };
document.addEventListener('submit', async e => {
  if (e.target.id === 'search-form') {
    e.preventDefault();
    e.stopImmediatePropagation();
    const query = new FormData(e.target).get('query');
    const resultBox = document.querySelector('#search-results');
    if (resultBox) resultBox.innerHTML = '<div class="muted">Searching…</div>';
    try {
      const results = await api(`/api/search?query=${encodeURIComponent(query)}&event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
      if (resultBox) resultBox.innerHTML = results.length ? results.map(result => `<div class="insight"><span class="kind">${esc(result.type)} · ${esc(result.session_title || result.session_id || '')}</span><strong>${esc(result.title || '')}</strong><p>${esc(result.snippet || '')}</p></div>`).join('') : '<div class="muted">No matching event evidence found.</div>';
    } catch (error) { if (resultBox) resultBox.innerHTML = `<div class="muted">${esc(error.message)}</div>`; }
    return;
  }
  if (e.target.id === 'analyst-form') {
    e.preventDefault();
    e.stopImmediatePropagation();
    const question = new FormData(e.target).get('question');
    const output = document.querySelector('#analyst-result');
    if (output) output.innerHTML = '<div class="muted">Searching event evidence…</div>';
    try {
      const result = await api('/api/analyst/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question, event_id:state.data?.event?.id})});
      if (output) output.innerHTML = `<div class="insight"><span class="kind">${esc(result.mode)} answer</span><p>${esc(result.answer)}</p>${(result.citations||[]).slice(0,3).map(source => `<small class="muted">${esc(source.session_title || source.session_id || 'Event source')} · ${esc(source.snippet || '')}</small>`).join('<br>')}</div>`;
    } catch (error) { if (output) output.innerHTML = `<div class="muted">${esc(error.message)}</div>`; }
    return;
  }
  if (e.target.id === 'question-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    try { await api('/api/questions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({body:form.get('body'), anonymous:form.get('anonymous') === 'on', session_id:activeSessionId()})}); e.target.reset(); document.querySelector('#question-status').textContent = 'Question submitted for organizer review.'; notify('Question submitted'); }
    catch (error) { notify(error.message); }
  }
  if (e.target.id === 'feedback-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    const value = name => form.get(name) ? Number(form.get(name)) : null;
    try { await api('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:activeSessionId(), speaker_rating:value('speaker_rating'), content_rating:value('content_rating'), session_rating:value('content_rating'), comment:form.get('comment')})}); e.target.reset(); notify('Feedback saved — thank you'); }
    catch (error) { notify(error.message); }
  }
}, true);

document.addEventListener('click', async event => {
  const anchor = event.target.closest('[data-attendee-anchor]')?.dataset.attendeeAnchor;
  if (anchor) {
    event.preventDefault();
    document.querySelectorAll('[data-attendee-anchor]').forEach(tab => tab.classList.toggle('active', tab === event.target.closest('[data-attendee-anchor]')));
    document.querySelector(`.${anchor}`)?.scrollIntoView({behavior:'smooth', block:'start'});
    return;
  }
  const topic = event.target.closest('[data-topic-label]')?.dataset.topicLabel;
  if (!topic) return;
  event.preventDefault();
  const evidence = document.querySelector('#topic-evidence');
  if (evidence) evidence.innerHTML = '<div class="muted">Finding supporting passages…</div>';
  try {
    const results = await api(`/api/search?query=${encodeURIComponent(topic)}&event_id=${encodeURIComponent(state.data?.event?.id || '')}`);
    if (evidence) evidence.innerHTML = results.slice(0, 4).map(result => `<div class="insight"><span class="kind">${esc(result.type)} · ${esc(result.session_title || result.session_id || '')}</span><p>${esc(result.snippet)}</p></div>`).join('') || '<div class="muted">No supporting passage found.</div>';
  } catch (error) { if (evidence) evidence.innerHTML = `<div class="muted">${esc(error.message)}</div>`; }
}, true);

document.addEventListener('click', async event => {
  const questionId = event.target.closest('[data-question-vote]')?.dataset.questionVote;
  if (!questionId) return;
  event.preventDefault();
  try {
    const question = await api(`/api/questions/${encodeURIComponent(questionId)}/votes`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({voter_id:'browser-anonymous'})});
    event.target.textContent = `${question.votes || 1} votes`;
    event.target.disabled = true;
  } catch (error) { notify(error.message); }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('.poll-response-form');
  if (!form) return;
  event.preventDefault();
  try { const values = new FormData(form); const voterKey = localStorage.getItem('smart-event-voter-id') || (crypto.randomUUID ? crypto.randomUUID() : `voter-${Date.now()}`); localStorage.setItem('smart-event-voter-id', voterKey); await api('/api/poll-responses', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({poll_id:form.dataset.pollId, attendee_id:voterKey, option_id:values.get('option_id') || null, option_ids:values.getAll('option_ids'), answer_text:values.get('answer_text') || null})}); form.reset(); document.querySelector('.audience-data-panel')?.remove(); await mountAudienceData(); notify('Poll response recorded'); }
  catch (error) { notify(error.message); }
}, true);

document.addEventListener('submit', async e => {
  if (e.target.id === 'capture-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    try {
      await api('/api/capture/text', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({text: form.get('text'), speaker: form.get('speaker'), language: form.get('language'), session_id: activeSessionId()}) });
      state.data = await api('/api/dashboard');
      e.target.reset();
      notify('Live capture saved to the event stream');
    } catch (error) { notify(error.message); }
  }
  if (e.target.id === 'upload-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    try {
      form.append('session_id', activeSessionId());
      form.append('event_id', state.data?.event?.id || '');
      const result = await api('/api/files/upload', { method: 'POST', body: form });
      state.data = await api('/api/dashboard');
      const status = document.querySelector('#upload-status');
      if (status) status.textContent = `Queued ${result.file?.original_name || 'upload'} for background transcription.`;
      notify('Upload queued — processing has started');
      const pollJob = async (attempt = 0) => {
        if (attempt > 12 || !result.job?.id) return;
        try {
          const jobs = await api(`/api/processing-jobs?session_id=${encodeURIComponent(activeSessionId())}`);
          const job = jobs.find(item => item.id === result.job.id);
          if (status && job) status.textContent = `${result.file?.original_name || 'Upload'} · ${job.status} · ${job.progress}%`;
          if (job && !['completed', 'failed'].includes(job.status)) return window.setTimeout(() => pollJob(attempt + 1), 1000);
          if (job?.status === 'completed') { state.data = await api('/api/dashboard'); notify('Upload processed and added to the event stream'); }
          if (job?.status === 'failed') notify(job.error_message || 'Upload processing failed');
        } catch (error) { if (status) status.textContent = error.message; }
      };
      pollJob();
    } catch (error) { notify(error.message); }
  }
}, true);

// Keep browser Back/Forward inside the single-page workspace.
if (!history.state?.view) history.replaceState({view: state.view}, '', `${location.pathname}${location.search}#${state.view}`);
document.addEventListener('click', e => {
  const target = e.target.closest('.nav-item:not(.capture-launch), [data-view-link]');
  if (!target) return;
  const next = target.dataset.view || target.dataset.viewLink;
  if (!next || next === 'landing') return;
  const route = next === 'auth' ? 'auth' : next;
  history.pushState({view: route}, '', `${location.pathname}${location.search}#${route}`);
}, true);
window.addEventListener('popstate', () => {
  const next = history.state?.view || 'landing';
  state.view = next;
  if (next === 'capture') {
    app.innerHTML = captureView();
    document.body.classList.remove('marketing');
    document.body.classList.add('workspace-view');
  } else {
    render();
  }
});

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-analyst-conversation]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const output = document.querySelector('#analyst-result');
  if (output) output.innerHTML = '<div class="muted">Loading conversation…</div>';
  try {
    const messages = await api(`/api/analyst/conversations/${encodeURIComponent(button.dataset.analystConversation)}/messages`);
    if (output) output.innerHTML = messages.map(message => `<div class="insight"><span class="kind">${esc(message.role)}</span><p>${esc(message.content)}</p>${(message.citations || []).slice(0, 3).map(source => `<small class="muted">${esc(source.session_title || source.session_id || 'Event source')} · ${esc(source.snippet || '')}</small>`).join('<br>')}</div>`).join('') || '<div class="muted">This conversation has no messages yet.</div>';
  } catch (error) { if (output) output.innerHTML = `<div class="muted">${esc(error.message)}</div>`; }
}, true);

// The public portal URL carries the share token so opening a QR code or an
// embedded WebView loads the same event data and records the portal visit.
connect = function() {
  const d = state.data;
  const token = d.share_links?.[0]?.token || 'gff-live';
  const link = `${location.origin}${location.pathname}?share=${encodeURIComponent(token)}#attendee`;
  const qr = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(link)}`;
return `${head('Distribution layer', 'Share & connect', 'Give every audience a doorway into the event — on stage, in the app, or in the follow-up email.', '<button class="btn lime" data-action="copy-link">Copy attendee link</button>')}<div class="grid connect-grid"><section class="card share-card"><h3>Attendee portal</h3><p>One clean destination for live takeaways, session details, questions, and post-event content.</p><div class="link-row"><input readonly value="${link}" id="share-link"/><button class="btn ghost" data-action="copy-link">Copy</button></div><div class="qr-wrap"><img class="qr-image" src="${qr}" alt="QR code for attendee portal"/><small>Place this QR on badges, screens, print, and speaker slides.<br><br><strong>${d.share_links?.[0]?.clicks || 0} portal opens</strong> from the current link.</small></div><div class="actions-row"><a class="btn" href="${link}">Open attendee portal ↗</a><button class="btn ghost" data-action="open-screen">Big-screen mode</button></div></section><section class="card share-card"><h3>Connected workflow</h3><p>Keep the event ecosystem moving with simple handoffs.</p><div class="insight"><span class="kind">LIVE DATA</span><strong>Supabase-ready data layer</strong><p>Events, sessions, attendees, insights, and share links are modeled for a direct database connection.</p></div><div class="insight"><span class="kind">EMBED KIT</span><strong>Put it inside your own app</strong><p>Use the portal URL inside an iframe, WebView, QR badge, email CTA, or event app deep link.</p></div><div class="actions-row"><button class="btn ghost" data-view-link="transcript">Live transcript</button><button class="btn ghost" data-action="copy-embed">Copy embed code</button></div></section></div>`;
};

const organizerViews = new Set(['overview', 'capture', 'sessions', 'attendees', 'content', 'connect', 'transcript', 'screen']);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-session-share]');
  if (!button) return;
  event.preventDefault();
  await navigator.clipboard?.writeText(button.dataset.sessionShare);
  notify('Session link copied');
}, true);
document.addEventListener('click', event => {
  const link = event.target.closest('[data-view-link]');
  const next = link?.dataset.viewLink;
  if (!link || !organizerViews.has(next) || state.authenticated) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  state.authMode = 'login';
  state.view = 'auth';
  render();
  notify('Organizer login required');
}, true);

document.addEventListener('click', async event => {
  if (event.target.closest('.auth-link') && state.authenticated) {
    event.preventDefault();
    event.stopImmediatePropagation();
    const session = JSON.parse(localStorage.getItem('smart-event-session') || '{}');
    try { await api('/api/auth/logout', {method:'POST', headers: session.access_token ? {Authorization:`Bearer ${session.access_token}`} : {}}); } catch (_) { /* local session is still cleared */ }
    localStorage.removeItem('smart-event-session');
    state.authenticated = false;
    state.view = 'landing';
    render();
    notify('Signed out safely');
    return;
  }
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'attendee-summary') {
    event.preventDefault();
    event.stopImmediatePropagation();
    const output = document.querySelector('#attendee-summary-output');
    if (output) output.innerHTML = '<div class="muted">Generating grounded summary…</div>';
    try {
      const result = await api(`/api/sessions/${encodeURIComponent(activeSessionId())}/summary`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({targetLanguage:'English'})});
      const summary = result.summary?.content || {};
      if (output) output.innerHTML = `<div class="insight"><span class="kind">${esc(result.mode || 'AI')} summary · saved</span><p>${esc(summary.output || '')}</p><small class="muted">${summary.evidence?.length || 0} linked transcript source${summary.evidence?.length === 1 ? '' : 's'}</small></div>`;
    } catch (error) { if (output) output.innerHTML = `<div class="muted">${esc(error.message)}</div>`; }
  }
  if (action === 'export-transcript') {
    event.preventDefault();
    event.stopImmediatePropagation();
    const format = event.target.closest('[data-format]')?.dataset.format || 'txt';
    try {
      const response = await fetch(`/api/transcripts/export?format=${format}`);
      if (!response.ok) throw new Error('Transcript export failed');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = `smart-event-transcript.${format}`; link.click(); URL.revokeObjectURL(url); notify(`Transcript ${format.toUpperCase()} downloaded`);
    } catch (error) { notify(error.message); }
  }
  if (action === 'copy-embed') {
    event.preventDefault();
    event.stopImmediatePropagation();
    const portal = document.querySelector('#share-link')?.value || attendeePortalUrl();
    await navigator.clipboard?.writeText(`<iframe src="${portal}" title="Smart Event Manager attendee portal" loading="lazy"></iframe>`);
    notify('Embed code copied');
  }
  if (action === 'share-social') {
    event.preventDefault();
    event.stopImmediatePropagation();
    const portal = document.querySelector('#share-link')?.value || attendeePortalUrl();
    try {
      if (navigator.share) await navigator.share({title:'Smart Event Manager event portal', text:'Join the live event experience', url:portal});
      else await navigator.clipboard?.writeText(portal);
      notify(navigator.share ? 'Share sheet opened' : 'Event link copied for social sharing');
    } catch (_) { notify('Event sharing was cancelled'); }
  }
}, true);

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="edit-asset"]');
  if (!button) return;
  event.preventDefault();
  const asset = state.assets.find(item => item.id === button.dataset.assetId);
  if (!asset) return notify('Asset is no longer available');
  const output = typeof asset.content === 'object' ? asset.content.output || '' : asset.content || '';
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop asset-editor"><div class="modal"><div class="eyebrow">Content studio · versioned edit</div><h2>Edit generated asset</h2><p class="muted">Save a new version manually or ask the grounded editor to reshape it without adding unsupported facts.</p><form id="asset-edit-form" class="form-grid" data-asset-id="${esc(asset.id)}"><label>Title<input name="title" required value="${esc(asset.title || '')}"></label><label>Editor instruction (optional)<input name="instruction" placeholder="Make this shorter and more executive"></label><label>Content<textarea name="output" rows="12" required>${esc(output)}</textarea></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button><button type="button" class="btn ghost" data-action="rewrite-asset">Apply AI edit</button><button class="btn lime">Save new version ↗</button></div></form></div></div>`);
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="copy-asset"]');
  if (!button) return;
  event.preventDefault();
  const asset = state.assets.find(item => item.id === button.dataset.assetId);
  const output = typeof asset?.content === 'object' ? asset.content.output || '' : asset?.content || '';
  if (!output) return notify('This asset has no copyable content yet');
  await navigator.clipboard?.writeText(output);
  notify('Asset content copied');
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="export-asset"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const session = JSON.parse(localStorage.getItem('smart-event-session') || '{}');
  try {
    const response = await fetch(`/api/content/assets/${encodeURIComponent(button.dataset.assetId)}/export?format=${encodeURIComponent(button.dataset.format || 'markdown')}`, {headers: session.access_token ? {Authorization:`Bearer ${session.access_token}`} : {}});
    if (!response.ok) { let detail = 'Asset export failed'; try { detail = (await response.json()).detail || detail; } catch (_) {} throw new Error(detail); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `${button.dataset.format || 'markdown'}-asset`; link.click(); URL.revokeObjectURL(url);
    notify('Content export downloaded');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="export-report"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const session = JSON.parse(localStorage.getItem('smart-event-session') || '{}');
  const format = button.dataset.format || 'markdown';
  try {
    const response = await fetch(`/api/reports/${encodeURIComponent(button.dataset.reportId)}/export?format=${encodeURIComponent(format)}`, {headers: session.access_token ? {Authorization:`Bearer ${session.access_token}`} : {}});
    if (!response.ok) { let detail = 'Report export failed'; try { detail = (await response.json()).detail || detail; } catch (_) {} throw new Error(detail); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `event-report.${format === 'markdown' ? 'md' : 'json'}`; link.click(); URL.revokeObjectURL(url);
    notify('Report export downloaded');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('submit', async event => {
  const form = event.target.closest('#asset-edit-form');
  if (!form) return;
  event.preventDefault();
  const values = new FormData(form);
  try {
    await api(`/api/content/assets/${encodeURIComponent(form.dataset.assetId)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:values.get('title'), content:{output:values.get('output')}})});
    form.closest('.modal-backdrop')?.remove();
    document.querySelector('.asset-library')?.remove();
    await mountAssetLibrary();
    notify('New content version saved');
  } catch (error) { notify(error.message); }
}, true);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="rewrite-asset"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const form = document.querySelector('#asset-edit-form');
  const instruction = form?.elements.instruction?.value?.trim();
  if (!form || !instruction) return notify('Enter an editing instruction first');
  button.disabled = true;
  try {
    const result = await api(`/api/content/assets/${encodeURIComponent(form.dataset.assetId)}/rewrite`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instruction, targetLanguage:'English'})});
    form.elements.output.value = result.output || form.elements.output.value;
    if (result.asset) state.assets = state.assets.map(asset => asset.id === result.asset.id ? result.asset : asset);
    notify(result.mode === 'fallback' ? 'Local editor preview applied' : 'Grounded AI edit applied');
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
}, true);
function showGeneratedContent(result) {
  document.querySelector('.generated-output')?.remove();
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop generated-output"><div class="modal generated-modal"><div class="eyebrow">${result.mode === 'fallback' ? 'Local content preview' : 'Gemini content intelligence'}</div><h2>Generated content</h2><p class="muted">${result.mode === 'fallback' ? 'The local preview is ready. Add valid Vertex credentials to replace it with Gemini output.' : `Generated with ${esc(result.model || 'Gemini')}.`}</p><pre>${esc(result.output || 'No content was returned.')}</pre><div class="modal-actions"><button class="btn lime" data-action="close-generated">Done</button></div></div></div>`);
}
document.addEventListener('click', async e => {
  const generate = e.target.closest('[data-action="generate"]');
  if (generate) {
    e.preventDefault();
    e.stopImmediatePropagation();
    const source = [...(state.data?.transcripts || []), ...(state.data?.insights || [])].map(item => item.text || `${item.title}: ${item.body}`).join('\n') || 'No transcript is available yet. Explain how an event team should prepare useful post-event takeaways.';
    const assetType = generate.dataset.contentType || 'attendee_recap';
    const assetTitle = generate.dataset.contentTitle || 'Attendee recap';
    generate.disabled = true;
    try {
      const result = await api('/api/content/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:source,targetLanguage:'English',asset_type:assetType,title:assetTitle,event_id:state.data?.event?.id,session_id:activeSessionId()})});
      showGeneratedContent(result);
      if (result.asset) state.assets = [result.asset, ...state.assets.filter(asset => asset.id !== result.asset.id)];
      document.querySelector('.asset-library')?.remove();
      await mountAssetLibrary();
    } catch (error) { notify(error.message); }
    finally { generate.disabled = false; }
    return;
  }
  if (e.target.closest('[data-action="close-generated"]')) e.target.closest('.generated-output')?.remove();
}, true);

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action="generate-takeaways"]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  button.disabled = true;
  const source = [...(state.data?.transcripts || []), ...(state.data?.insights || [])].map(item => item.text || `${item.title}: ${item.body}`).join('\n');
  try {
    await api('/api/takeaways/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:source, event_id:state.data?.event?.id, session_id:activeSessionId(), title:'Session takeaway'})});
    document.querySelector('.takeaway-panel')?.remove();
    await mountTakeawayPanel();
    notify('Evidence-linked takeaway saved');
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
}, true);

document.addEventListener('submit', async event => {
  const form = event.target.closest('#brand-kit-form');
  if (!form) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const fields = Object.fromEntries(new FormData(form));
  try {
    const kit = await api('/api/brand-kit', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...fields, organization_id:state.data?.event?.organization_id || 'org-demo'})});
    state.brandKit = kit;
    if (kit.primary_color) document.documentElement.style.setProperty('--purple', kit.primary_color);
    if (kit.secondary_color) document.documentElement.style.setProperty('--ink', kit.secondary_color);
    if (kit.accent_color) document.documentElement.style.setProperty('--lime', kit.accent_color);
    notify('Brand kit saved for future content');
  } catch (error) { notify(error.message); }
}, true);
