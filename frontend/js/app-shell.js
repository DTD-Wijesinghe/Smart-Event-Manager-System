const state = { view: 'landing', data: null, authenticated: false, authMode: 'login', resetToken: '' };
const app = document.querySelector('#app');
const toast = document.querySelector('#toast');

const esc = (value = '') => String(value).replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
const date = iso => new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
const money = num => new Intl.NumberFormat('en', { notation:'compact', maximumFractionDigits:1 }).format(num);
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
  }
  if (!response.ok) throw new Error(payload?.detail || payload?.error || 'Request failed');
  return payload;
}
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
  state.view = 'landing';
  render();
  try {
    state.data = await api('/api/dashboard');
    const shareToken = new URLSearchParams(location.search).get('share');
    if (shareToken) {
      const shared = await api(`/api/public/share/${encodeURIComponent(shareToken)}`);
      state.data = {...state.data, event: shared.event || state.data.event, sessions: shared.sessions || state.data.sessions, share_links: [shared.link]};
      state.view = 'attendee';
    } else if (state.authenticated) {
      state.view = 'overview';
    }
    if (state.view !== 'landing') render();
  } catch (error) {
    if (state.view !== 'landing') {
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
    notify(`Workspace data is unavailable: ${error.message}`);
  }
}

function head(eyebrow, title, description, actions = '') { return `<div class="view-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${description}</p></div><div class="actions">${actions}</div></div>`; }
function landing() { return `<div class="marketing-page"><nav class="landing-nav"><a class="landing-brand" href="#landing"><span class="logo"><i></i></span><span>smart event <b>manager</b></span></a><div class="landing-links"><a href="#platform">Platform</a><a href="#workflow">Workflow</a><a href="#use-cases">Use cases</a><a href="#trust">Trust</a></div><div class="landing-actions"><button class="landing-login" data-view-link="auth" data-auth-mode="login">Log in</button><button class="landing-register" data-view-link="auth" data-auth-mode="register">Register</button><button class="btn lime" data-view-link="auth" data-auth-mode="login">Book a walkthrough ↗</button></div></nav><section class="landing-hero"><div class="hero-copy"><div class="eyebrow"><i></i> Event content intelligence</div><h1>Every voice.<br><span>One clear signal.</span></h1><p>Smart Event Manager captures the live room, turns conversations into intelligence, and keeps your event creating value long after the lights go down.</p><div class="actions"><button class="btn lime" data-view-link="auth" data-auth-mode="login">See it in action ↗</button><button class="landing-text-link" data-view-link="auth" data-auth-mode="register">Create your event workspace <span>↗</span></button></div><div class="hero-proof"><span class="proof-dot"></span> Trusted by teams running the world’s most important rooms <span class="proof-lines">01 · 02 · 03</span></div></div><div class="hero-visual"><div class="visual-glow"></div><div class="visual-card live-board"><div class="visual-top"><span>GLOBAL FUTURES FORUM</span><b>● LIVE</b></div><div class="visual-wave">${[30,52,38,75,45,90,62,78,40,86,58,72,48,95,57,80].map(h=>`<i style="height:${h}%"></i>`).join('')}</div><div class="visual-insight"><small>LIVE SYNTHESIS · 02:14 AGO</small><strong>Collective response is the new advantage.</strong><p>68 signals · 24 languages · 1,842 in the room</p></div></div><div class="floating-card float-a"><span>75+</span><small>LANGUAGES</small></div><div class="floating-card float-b"><span>20+</span><small>CONTENT FORMATS</small></div></div></section><section class="landing-strip"><span>Designed for the moments that matter</span><span>CONFERENCES</span><span>TRADE SHOWS</span><span>COMPANY EVENTS</span><span>ASSOCIATIONS</span></section><section class="landing-section" id="workflow"><div class="landing-section-head"><div><div class="eyebrow">The event content engine</div><h2>From live moment<br>to lasting momentum.</h2></div><p>One event creates hundreds of valuable moments. Capture them once, then make them work everywhere.</p></div><div class="landing-flow"><article><div class="flow-number">01</div><div class="flow-symbol">◌</div><h3>Capture</h3><p>Plug into your live audio, virtual stage, or event recording. Every voice becomes searchable signal.</p><a href="#platform">See live intelligence ↗</a></article><article><div class="flow-number">02</div><div class="flow-symbol">✦</div><h3>Synthesize</h3><p>Find the themes, questions, quotes, and decisions that deserve to move through your organization.</p><a href="#platform">See insights ↗</a></article><article><div class="flow-number">03</div><div class="flow-symbol">↗</div><h3>Remix</h3><p>Turn one session into recaps, briefs, speaker packs, social moments, and an attendee knowledge layer.</p><a href="#platform">See content studio ↗</a></article></div></section><section class="landing-section dark-section" id="platform"><div class="landing-section-head"><div><div class="eyebrow">One platform, every phase</div><h2>Make the room<br>work harder.</h2></div><p>Keep people present during the event, then give every idea a longer life after it.</p></div><div class="platform-grid"><div class="platform-feature feature-large"><span class="feature-tag">LIVE</span><h3>Intelligence while it’s happening.</h3><p>Live summaries, transcript, translation, audience questions, and signal maps — without pulling people out of the moment.</p><div class="feature-orbit"></div></div><div class="platform-feature feature-lilac"><span class="feature-tag">INSIGHTS</span><h3>Clarity for every stakeholder.</h3><p>Executive reports, theme clusters, intent signals, and sponsor-ready proof.</p></div><div class="platform-feature feature-lime"><span class="feature-tag">REMIX</span><h3>Content that keeps going.</h3><p>Branded portals, social-ready moments, speaker packs, and follow-up journeys.</p></div></div></section><section class="landing-section" id="use-cases"><div class="landing-section-head"><div><div class="eyebrow">Built around the way you work</div><h2>Your event.<br>Your signal.</h2></div><p>From a 50-person summit to a multi-stage conference, Smart Event Manager adapts to the room.</p></div><div class="case-grid"><div><span>01</span><h3>Keep attendees present</h3><p>Give people the takeaways without making them choose between the stage and their notes.</p></div><div><span>02</span><h3>Prove event value</h3><p>Show leadership and sponsors what resonated, what connected, and what moved next.</p></div><div><span>03</span><h3>Extend the event</h3><p>Turn three days of energy into a knowledge layer that works for the next 362.</p></div></div></section><section class="landing-cta" id="trust"><div><div class="eyebrow">Ready when the room is</div><h2>Don’t let the good part end at goodbye.</h2><p>See the full Smart Event Manager workflow in your next event.</p></div><button class="btn lime" data-view-link="auth" data-auth-mode="login">Book a walkthrough ↗</button></section><footer class="landing-footer"><span>© 2026 Smart Event Manager</span><div><a href="#workflow">Workflow</a><a href="#platform">Platform</a><a href="#use-cases">Use cases</a><button data-view-link="auth" data-auth-mode="login">Organizer access ↗</button></div></footer></div>`; }
function overview() {
  const d = state.data, live = d.sessions.find(s => s.status === 'live');
  return `${head('Saturday · September 26, 2026', 'Good morning, Leila.', 'Your command center for every moment before, during, and after Global Futures Forum.', '<button class="btn ghost" data-action="share">↗ Share portal</button><button class="btn lime" data-action="add-session">+ Add session</button>')}
  <div class="grid kpis"><div class="card kpi"><small>Checked in</small><strong>${money(d.attendees.filter(a=>a.checked_in).length * 1000 + 842)}</strong><footer><span class="up">↗ 12.4%</span><span>vs. last event</span></footer></div><div class="card kpi"><small>Live attendance</small><strong>${money(live.attendance)}</strong><footer><span class="up">↗ 8.1%</span><span>${live.title}</span></footer></div><div class="card kpi"><small>Content signals</small><strong>68</strong><footer><span class="up">↗ 14 new</span><span>this session</span></footer></div><div class="card kpi"><small>Intent score</small><strong>82%</strong><footer><span class="up">Healthy</span><span>across attendees</span></footer></div></div>
  <div class="grid overview-grid"><section class="card live-card"><div class="card-head"><h2>Run of show</h2><a href="#sessions" data-view-link="sessions">View full agenda ↗</a></div><div class="timeline">${d.sessions.map(s=>`<div class="timeline-row"><span class="time">${date(s.starts_at)}</span><span class="dot ${s.status==='live'?'live':''}"></span><div><strong>${esc(s.title)}</strong><small>${esc(s.track)} · ${esc(s.room)}</small></div><span class="session-status ${s.status==='live'?'live':''}">${s.status==='live'?'Live now':'Upcoming'}</span></div>`).join('')}</div></section>
  <section class="card"><div class="card-head"><h2>Live intelligence</h2><a href="#content" data-view-link="content">Open studio ↗</a></div><div class="insight-list">${d.insights.map(i=>`<div class="insight"><span class="kind">${esc(i.kind)} · ${Math.round(i.confidence*100)}% confidence</span><strong>${esc(i.title)}</strong><p>${esc(i.body)}</p></div>`).join('')}<div class="signal">${[32,48,29,66,53,82,58,91,47,77,60,86,39,72,65,93,55,68].map(h=>`<i style="height:${h}%"></i>`).join('')}</div></div></section></div>`;
}
function sessions() { const d = state.data; return `${head('Program builder', 'Sessions', 'Keep the agenda clear, the rooms moving, and every talk ready to become a useful takeaway.', '<button class="btn ghost" data-action="export">↓ Export agenda</button><button class="btn lime" data-action="add-session">+ Add session</button>')}<section class="card section-card"><div class="card-head"><h2>All sessions · ${d.sessions.length}</h2><a href="#overview" data-view-link="overview">Back to overview</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Session</th><th>Track / room</th><th>Speaker</th><th>Time</th><th>Attendance</th><th>Status</th></tr></thead><tbody>${d.sessions.map(s=>`<tr><td><strong>${esc(s.title)}</strong><br><span class="muted">${esc(s.summary)}</span></td><td>${esc(s.track)}<br>${esc(s.room)}</td><td>${esc(s.speaker)}</td><td>${date(s.starts_at)}<br>${date(s.ends_at)}</td><td>${money(s.attendance)}</td><td><span class="badge ${s.status==='live'?'orange':'green'}">${s.status}</span></td></tr>`).join('')}</tbody></table></div></section>`; }
function attendees() { const d = state.data; return `${head('Audience intelligence', 'Attendees', 'Understand who is in the room, what they care about, and where the next meaningful connection can happen.', '<button class="btn ghost" data-action="export">↓ Export CSV</button><button class="btn purple" data-action="match">✦ Find matches</button>')}<section class="card section-card"><div class="card-head"><h2>Attendee signal map · ${d.attendees.length} loaded</h2><a href="#connect" data-view-link="connect">Networking tools ↗</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Person</th><th>Company / role</th><th>Interests</th><th>Intent score</th><th>Check-in</th></tr></thead><tbody>${d.attendees.map(a=>`<tr><td><strong>${esc(a.full_name)}</strong></td><td>${esc(a.company)}<br>${esc(a.role)}</td><td>${a.interests.map(i=>`<span class="badge">${esc(i)}</span>`).join(' ')}</td><td class="score">${a.intent_score}</td><td><span class="badge ${a.checked_in?'green':''}">${a.checked_in?'Checked in':'Not yet'}</span></td></tr>`).join('')}</tbody></table></div></section>`; }
function content() { return `${head('Content engine', 'Content studio', 'Turn live signals into a clean stream of assets for attendees, speakers, sponsors, and leadership.', '<button class="btn ghost" data-action="share">↗ Share selected</button><button class="btn lime" data-action="generate">✦ Generate recap</button>')}<div class="grid studio-grid"><article class="card studio-card purple"><span class="studio-icon">✦</span><h3>Executive brief</h3><p>One sharp page of the themes, tensions, and decisions that leadership needs next.</p><button class="btn lime" data-action="generate">Generate brief ↗</button></article><article class="card studio-card lime"><span class="studio-icon">◌</span><h3>Attendee recap</h3><p>A branded, multilingual digest that helps the room keep learning after the closing keynote.</p><button class="btn" data-action="generate">Create recap ↗</button></article><article class="card studio-card blue"><span class="studio-icon">◒</span><h3>Speaker remix</h3><p>Give every speaker a quote bank, session summary, and moments ready for social.</p><button class="btn ghost" data-action="generate">Build speaker pack ↗</button></article><article class="card studio-card"><span class="studio-icon">▦</span><h3>Signal library</h3><p>Browse the strongest quotes, audience questions, topic clusters, and audio moments.</p><button class="btn ghost" data-action="library">Open library ↗</button></article></div>`; }
function connect() { const d = state.data, link = `${location.origin}/attendee/${d.event.slug}`; const qr = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(link)}`; return `${head('Distribution layer', 'Share & connect', 'Give every audience a doorway into the event — on stage, in the app, or in the follow-up email.', '<button class="btn lime" data-action="copy-link">Copy attendee link</button>')}<div class="grid connect-grid"><section class="card share-card"><h3>Attendee portal</h3><p>One clean destination for live takeaways, session details, questions, and post-event content.</p><div class="link-row"><input readonly value="${link}" id="share-link"/><button class="btn ghost" data-action="copy-link">Copy</button></div><div class="qr-wrap"><img class="qr-image" src="${qr}" alt="QR code for attendee portal"/><small>Place this QR on badges, screens, print, and speaker slides.<br><br><strong>${d.share_links[0].clicks} portal opens</strong> from the current link.</small></div><div class="actions-row"><button class="btn" data-view-link="attendee">Open attendee portal ↗</button><button class="btn ghost" data-view-link="screen">Big-screen mode</button></div></section><section class="card share-card"><h3>Connected workflow</h3><p>Keep the event ecosystem moving with simple handoffs.</p><div class="insight"><span class="kind">LIVE DATA</span><strong>Supabase-ready data layer</strong><p>Events, sessions, attendees, insights, and share links are modeled for a direct database connection.</p></div><div class="insight"><span class="kind">EMBED KIT</span><strong>Put it inside your own app</strong><p>Use the portal URL inside an iframe, WebView, QR badge, email CTA, or event app deep link.</p></div><div class="actions-row"><button class="btn ghost" data-view-link="transcript">Live transcript</button><button class="btn ghost" data-action="copy-embed">Copy embed code</button></div></section></div>`; }
function attendee() { const d=state.data, link=`${location.origin}/attendee/${d.event.slug}`; return `<div class="attendee-view"><div class="attendee-nav"><a class="brand attendee-brand" href="#overview"><span class="logo"><i></i></span><span>smart event <b>manager</b></span></a><span class="attendee-live"><i></i> LIVE EXPERIENCE</span><button class="btn ghost" data-view-link="overview">Organizer view</button></div><div class="attendee-hero"><div><div class="eyebrow">Global Futures Forum · Singapore</div><h1>Stay in the room.<br><span>Take the insight with you.</span></h1><p>Live takeaways, language support, audience questions, and the moments worth sharing — all in one event companion.</p><div class="actions"><button class="btn lime" data-view-link="transcript">Open live transcript</button><button class="btn ghost" data-action="share-social">Share event ↗</button></div></div><div class="attendee-card"><div class="card-head"><h2>Now on stage</h2><span class="badge orange">LIVE</span></div><div class="attendee-session"><small>MAIN STAGE · SESSION 04</small><strong>Designing for what’s next</strong><span>Maya Chen · Future Systems</span></div><div class="translation-row"><span>◎ English</span><select id="language"><option>English</option><option>Spanish</option><option>French</option><option>Japanese</option><option>Arabic</option></select><button class="btn purple" data-view-link="transcript">Translate</button></div></div></div><div class="attendee-tiles"><article><span>✦</span><h3>Live takeaways</h3><p>Keep up with the strongest ideas even when you move between stages.</p></article><article><span>◌</span><h3>Ask the room</h3><p>Submit questions and see what the audience is curious about right now.</p></article><article><span>↗</span><h3>Make it travel</h3><p>Share a quote, recap, or social-ready moment with your network.</p></article></div><div class="attendee-footer"><span>Event link: ${link}</span><button class="btn ghost" data-action="share-social">Share to social ↗</button></div></div>`; }
function screen() { const d=state.data, live=d.sessions.find(s=>s.status==='live'); return `<div class="screen-view"><div class="screen-top"><span class="brand attendee-brand"><span class="logo"><i></i></span> smart event manager</span><span><i class="screen-dot"></i> LIVE · GLOBAL FUTURES FORUM</span></div><div class="screen-content"><div class="eyebrow">MAIN STAGE · SESSION 04</div><h1>${esc(live.title)}</h1><p>${esc(live.summary)}</p><div class="screen-signal">${[40,62,30,82,54,76,46,90,61,72,53,86,68,96,50,77].map(h=>`<i style="height:${h}%"></i>`).join('')}</div><div class="screen-bottom"><span>Ask a question · Scan to join · 75+ languages</span><button class="btn lime" data-view-link="attendee">Open attendee portal ↗</button></div></div></div>`; }
function transcript() { const d=state.data; return `<div class="transcript-view">${head('Live language layer', 'Live transcript', 'Turn every spoken moment into readable, searchable intelligence for every attendee.', '<button class="btn ghost" data-view-link="attendee">Attendee view</button><button class="btn lime" data-action="copy-link">Share transcript</button>')}<div class="transcript-toolbar card"><span class="badge orange">● LIVE</span><strong>Designing for what’s next</strong><span class="muted">Maya Chen · Main stage</span><select><option>English · Original</option><option>Spanish · Auto-translated</option><option>French · Auto-translated</option><option>Japanese · Auto-translated</option></select></div><div class="transcript-grid"><section class="card transcript-card"><div class="transcript-line"><span>09:32:18</span><p>We often ask whether an organization can predict what is coming next.</p></div><div class="transcript-line active"><span>09:32:31</span><p>But the stronger question is whether we have built the habit of responding together.</p></div><div class="transcript-line"><span>09:32:47</span><p>That is what turns uncertainty from a threat into a shared design problem.</p></div><div class="transcript-line"><span>09:33:02</span><p>And it is a practice every team can start today.</p></div></section><aside class="card translation-card"><div class="card-head"><h2>Language coverage</h2></div><div class="language-list">${['English','Spanish','French','Japanese','Arabic','Portuguese'].map((l,i)=>`<div><span>${l}</span><b>${i<3?'Live':'Queued'}</b></div>`).join('')}</div></aside></div></div>`; }
function auth() { return `<div class="auth-view"><div class="auth-art"><div class="auth-orbit"></div><div class="eyebrow">Organizer workspace</div><h1>Run the room.<br><span>Keep the signal.</span></h1><p>Smart Event Manager gives event teams one place to plan, operate, share, and prove the value of every live moment.</p><div class="auth-proof"><span>◈</span> Live intelligence <span>✦</span> Shareable content <span>◎</span> Global access</div></div><div class="auth-card card"><div class="auth-tabs"><button class="auth-tab active" data-auth-tab="login">Log in</button><button class="auth-tab" data-auth-tab="register">Create account</button></div><div id="auth-form-wrap"></div></div></div>`; }
function authForm(kind='login') { const config={login:['Welcome back','Log in to your organizer workspace','Log in'],register:['Create your workspace','Start building your next great event','Create account'],forgot:['Reset your password','We’ll send a secure reset link to your inbox','Send reset link']}[kind]; const password=kind!=='forgot'?`<label>Password<input type="password" name="password" placeholder="At least 8 characters" required></label>${kind==='login'?'<button type="button" class="forgot-inline-toggle" data-action="forgot-inline" style="border:0;background:none;color:var(--purple);font-size:11px;font-weight:800;padding:7px 0 0;text-align:left">Forgot password?</button><div class="forgot-inline" hidden><label>Reset email<input type="email" name="resetEmail" placeholder="you@company.com"></label><button type="button" class="btn ghost reset-inline-button" data-action="send-reset" style="margin-top:6px">Send reset link</button></div>':''}`:''; return `<form class="auth-form" id="${kind}-form"><h2>${config[0]}</h2><p>${config[1]}</p><label>Email address<input type="email" name="email" placeholder="you@company.com" required></label>${password}${kind==='register'?'<label>Confirm password<input type="password" name="confirm" placeholder="Repeat your password" required></label>':''}<button class="btn purple">${config[2]} ↗</button><small>By continuing, you agree to the workspace terms and event data policy.</small></form>`; }
function resetAuthForm() { return `<form class="auth-form" id="reset-form"><h2>Choose a new password</h2><p>Create a new secure password for your organizer account.</p><label>New password<input type="password" name="password" placeholder="At least 8 characters" required></label><label>Confirm password<input type="password" name="confirm" placeholder="Repeat your password" required></label><button class="btn purple">Update password ?</button><small>Your recovery link is used only for this password update.</small></form>`; }
function render() { const views = { landing, overview, sessions, attendees, content, connect, attendee, screen, transcript, auth }; app.innerHTML = views[state.view](); const fullBleed = ['landing','auth','attendee','screen','transcript'].includes(state.view); document.body.classList.toggle('marketing', fullBleed); document.body.classList.toggle('workspace-view', !fullBleed); document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active', b.dataset.view===state.view)); if(state.view==='auth'){const wrap=document.querySelector('#auth-form-wrap');wrap.innerHTML=state.authMode==='reset'?resetAuthForm():authForm(state.authMode);document.querySelectorAll('[data-auth-tab]').forEach(tab=>tab.addEventListener('click',()=>{state.authMode=tab.dataset.authTab;document.querySelectorAll('[data-auth-tab]').forEach(x=>x.classList.toggle('active',x===tab));wrap.innerHTML=authForm(state.authMode)}));} }
function modal() { if (document.querySelector('.modal-backdrop')) return; document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal"><div class="eyebrow">Add to the live program</div><h2>Create a session</h2><p>New sessions are saved to the local demo store, or directly to Supabase when your environment keys are configured.</p><form class="form-grid" id="session-form"><label>Session title<input name="title" required placeholder="e.g. Designing for the next decade"/></label><label>Track<select name="track"><option>Main stage</option><option>Leadership</option><option>Growth</option></select></label><label>Speaker<input name="speaker" placeholder="Name · Company"/></label><label>Room<input name="room" placeholder="Room 101"/></label><div class="modal-actions"><button type="button" class="btn ghost" data-action="close-modal">Cancel</button><button class="btn lime">Create session</button></div></form></div></div>`); }
document.addEventListener('click', async e => { const nav=e.target.closest('.nav-item'); if(nav){state.view=nav.dataset.view;render();return} const viewLink=e.target.closest('[data-view-link]'); if(viewLink){e.preventDefault();const next=viewLink.dataset.viewLink;if(next==='auth'){if(state.authenticated){state.view='overview'}else{state.authMode=viewLink.dataset.authMode || 'login';state.view='auth'}}else{state.view=next}render();return} const action=e.target.closest('[data-action]')?.dataset.action; if(!action)return; if(action==='forgot-inline'){const box=document.querySelector('.forgot-inline');if(box){box.hidden=!box.hidden;e.target.textContent=box.hidden?'Forgot password?':'Hide password reset';}return} if(action==='send-reset'){notify('If the email exists, a reset link is on its way');return} if(action==='add-session'){modal();return} if(action==='close-modal'){document.querySelector('.modal-backdrop')?.remove();return} if(action==='copy-link'){await navigator.clipboard?.writeText(document.querySelector('#share-link')?.value || `${location.origin}/attendee/global-futures-forum`);notify('Attendee link copied to clipboard');return} if(action==='copy-embed'){await navigator.clipboard?.writeText(`<iframe src="${location.origin}/attendee/global-futures-forum" title="Global Futures Forum attendee portal"></iframe>`);notify('Embed code copied');return} if(action==='share-social'){notify('Social share card prepared — copy the event link to post it');return} if(action==='generate'){try{const source=(state.data?.insights||[]).map(item=>item.title+': '+item.body).join('\n') || 'No transcript is available yet. Explain how an event team should prepare useful post-event takeaways.';const result=await api('/api/ai/summarize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:source,targetLanguage:'English'})});notify(`Gemini generated a ${result.model} content brief`)}catch(error){notify(error.message.includes('GEMINI_API_KEY')?'Add GEMINI_API_KEY to the server environment first':error.message)}return} if(action==='match'){notify('Finding high-intent attendee matches');return} if(action==='export'){notify('Export prepared for download');return} if(action==='library'){state.view='content';render();notify('Signal library opened');return} if(action==='share'){state.view='connect';render();return} });
document.addEventListener('submit', async e => { if(e.target.id==='session-form'){e.preventDefault(); const payload=Object.fromEntries(new FormData(e.target)); await api('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); document.querySelector('.modal-backdrop')?.remove(); state.data=await api('/api/dashboard'); state.view='sessions'; render(); notify('Session added to the program'); return;} if(e.target.id==='login-form'){e.preventDefault();state.authenticated=true;await openWorkspace('Welcome back - organizer workspace ready');return} if(e.target.id==='register-form'){e.preventDefault();const f=new FormData(e.target);if(f.get('password')!==f.get('confirm')){notify('Passwords do not match');return}state.authenticated=true;await openWorkspace('Workspace created - welcome to Smart Event Manager');return} if(e.target.id==='forgot-form'){e.preventDefault();notify('If the email exists, a reset link is on its way');return} });
// Auth is handled by the backend/Supabase Auth. The capture-phase listener
// keeps the existing UI and prevents the old demo-only submit handler below
// from marking a user authenticated before the server accepts the request.
document.addEventListener('click', async e => { const action=e.target.closest('[data-action="send-reset"]')?.dataset.action; if(action!=='send-reset')return; e.preventDefault(); e.stopImmediatePropagation(); const email=document.querySelector('input[name="resetEmail"]')?.value || document.querySelector('#login-form input[name="email"]')?.value; if(!email)return notify('Enter your email address first'); try{await api('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});notify('If the email exists, a reset link is on its way')}catch(error){notify(error.message)} }, true);
document.addEventListener('submit', async e => { const form=e.target; if(!['login-form','register-form','forgot-form'].includes(form.id))return; e.preventDefault(); e.stopImmediatePropagation(); const fields=Object.fromEntries(new FormData(form)); try { if(form.id==='register-form' && fields.password!==fields.confirm) throw new Error('Passwords do not match'); const endpoint=form.id==='login-form'?'/api/auth/login':form.id==='register-form'?'/api/auth/register':'/api/auth/forgot-password'; const payload=form.id==='forgot-form'?{email:fields.email}:{email:fields.email,password:fields.password}; const result=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if(form.id==='forgot-form'){notify('If the email exists, a reset link is on its way');return} state.authenticated=true; localStorage.setItem('smart-event-session',JSON.stringify(result.session||{})); await openWorkspace(form.id==='login-form'?'Welcome back - organizer workspace ready':'Workspace created - welcome to Smart Event Manager'); } catch(error){notify(error.message)} }, true);
document.querySelector('#mobileMenu').addEventListener('click',()=>document.querySelector('.sidebar').classList.toggle('open'));
document.addEventListener('change', async e => { const selector=e.target.closest('.transcript-toolbar select'); if(!selector)return; const language=(selector.value||'English').split(' ')[0]; if(language==='English')return notify('Original English transcript selected'); const source=[...document.querySelectorAll('.transcript-card p')].map(item=>item.textContent).join('\n'); const status=document.querySelector('#translation-status'); if(status)status.textContent='Translating…'; try{const result=await api('/api/ai/translate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:source,targetLanguage:language})}); let output=document.querySelector('.translation-output'); if(!output){document.querySelector('.transcript-card')?.insertAdjacentHTML('afterbegin','<div class="translation-output insight"></div>');output=document.querySelector('.translation-output')} output.innerHTML=`<span class="kind">${esc(result.targetLanguage)} translation</span><p>${esc(result.output)}</p>`; if(status)status.textContent=result.mode==='fallback'?'Local preview':'Live'; }catch(error){if(status)status.textContent=error.message} });
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
<div class="capture-grid"><section class="card capture-card"><div class="capture-card-top"><div><span class="capture-icon">◉</span><h2>Live capture</h2><p>Use your laptop microphone or mixer feed. This browser capture is ideal for a quick test; production audio can come from your venue mixer or meeting bot.</p></div><span class="capture-status" id="capture-status">Idle</span></div><div class="capture-controls"><button class="btn lime" data-capture-action="start">Start microphone</button><button class="btn ghost" data-capture-action="stop" disabled>Stop</button></div><div class="capture-meter"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div><small class="muted">Use headphones and ask the speaker for consent before capturing.</small></section>
<section class="card capture-card"><div class="capture-card-top"><div><span class="capture-icon">↥</span><h2>Upload a recording</h2><p>Drop MP3, WAV, M4A, or MP4 files for batch transcription and post-event synthesis.</p></div></div><form id="upload-form" class="upload-form"><label class="upload-drop"><input type="file" name="file" accept="audio/*,video/mp4" required><strong>Choose a recording</strong><span>Up to 20 MB in this starter workspace</span></label><select name="language"><option value="auto">Detect language</option><option>English</option><option>Tamil</option><option>Spanish</option><option>French</option><option>Japanese</option></select><button class="btn purple">Transcribe recording ↗</button></form></section></div>
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
          form.append('session_id', 'ses-001');
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
      await api('/api/sessions/ses-001/start', {method:'POST'}).catch(() => null);
      document.querySelector('#capture-status').textContent = 'Listening';
      document.querySelector('[data-capture-action="start"]').disabled = true;
      document.querySelector('[data-capture-action="stop"]').disabled = false;
      notify('Microphone connected — recording live audio');
    } catch (_) { notify('Microphone permission was not granted'); }
  } else if (captureAction === 'stop') {
    if (window.__captureRecorder?.state === 'recording') window.__captureRecorder.stop();
    window.__captureStream?.getTracks().forEach(track => track.stop());
    await api('/api/sessions/ses-001/stop', {method:'POST'}).catch(() => null);
    document.querySelector('#capture-status').textContent = 'Processing';
    document.querySelector('[data-capture-action="start"]').disabled = false;
    document.querySelector('[data-capture-action="stop"]').disabled = true;
    notify('Microphone stopped — processing the recording');
  }
}, true);

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
  card.innerHTML = rows.slice(0, 24).reverse().map((row, index) => `<div class="transcript-line ${index === rows.length - 1 ? 'active' : ''}"><span>${row.created_at ? new Date(row.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : 'LIVE'}</span><p>${esc(row.text || '')}</p><small class="muted">${esc(row.speaker || 'Live speaker')} · ${esc(row.language || 'auto')}</small></div>`).join('');
}
function mountAnalystPanel() {
  if (state.view !== 'content' || document.querySelector('.analyst-panel')) return;
  document.querySelector('.page')?.insertAdjacentHTML('beforeend', '<section class="card section-card analyst-panel"><div class="card-head"><div><h2>Ask the event analyst</h2><p class="muted">Ask a question about captured sessions. Answers stay grounded in your event evidence.</p></div><span class="badge">Source linked</span></div><form id="analyst-form" class="capture-form"><textarea name="question" rows="3" required placeholder="What themes or opportunities appeared across the event?"></textarea><div class="capture-form-row"><button class="btn purple">Ask analyst ↗</button></div></form><div id="analyst-result" class="insight-list"></div></section>');
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
    const [questions, polls] = await Promise.all([api('/api/questions?session_id=ses-001'), api('/api/polls?session_id=ses-001')]);
    const questionFeed = document.querySelector('#question-feed');
    if (questionFeed) questionFeed.innerHTML = questions.length ? questions.map(question => `<div class="insight"><span class="kind">${question.status || 'pending'} · ${question.votes || 0} votes</span><p>${esc(question.body)}</p><button class="btn ghost" data-question-vote="${esc(question.id)}">Upvote</button></div>`).join('') : '<span class="muted">No audience questions yet.</span>';
    const pollFeed = document.querySelector('#poll-feed');
    if (pollFeed) pollFeed.innerHTML = polls.length ? polls.map(poll => `<div class="insight"><span class="kind">${poll.is_open ? 'Live poll' : 'Poll'}</span><strong>${esc(poll.question)}</strong><form class="poll-response-form" data-poll-id="${esc(poll.id)}"><input name="answer_text" placeholder="Your response" required><button class="btn lime">Respond ↗</button></form></div>`).join('') : '';
  } catch (error) { const feed = document.querySelector('#question-feed'); if (feed) feed.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
}
function mountAttendeeTabs() {
  if (state.view !== 'attendee' || document.querySelector('.attendee-tabs')) return;
  document.querySelector('.attendee-nav')?.insertAdjacentHTML('afterend', '<nav class="attendee-tabs" aria-label="Attendee portal sections"><button class="active" data-attendee-anchor="attendee-hero">Live</button><button data-attendee-anchor="attendee-summary-panel">Summary</button><button data-attendee-anchor="topic-cloud-panel">Idea cloud</button><button data-attendee-anchor="audience-data-panel">Q&A + Polls</button><button data-attendee-anchor="attendee-interactions">Feedback</button></nav>');
}
function mountAttendeeSummary() {
  if (state.view !== 'attendee' || document.querySelector('.attendee-summary-panel')) return;
  document.querySelector('.attendee-view')?.insertAdjacentHTML('beforeend', '<section class="attendee-interactions attendee-summary-panel"><div class="attendee-interaction-card"><div class="eyebrow">Session intelligence</div><h2>Session summary</h2><p>Generate a grounded recap from the latest captured signals.</p><button class="btn lime" data-action="attendee-summary">Generate summary ↗</button><div id="attendee-summary-output" class="insight-list"></div></div></section>');
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
const baseRender = render;
render = function wrappedRender() { baseRender(); mountLiveMetrics(); if (state.view === 'attendee') mountAttendeeInteractions(); mountTranscriptData(); mountAnalystPanel(); mountSearchPanel(); mountTopicCloud(); mountAudienceData(); mountAttendeeTabs(); mountAttendeeSummary(); mountTranscriptActions(); };
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
    try { await api('/api/questions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({body:form.get('body'), anonymous:form.get('anonymous') === 'on', session_id:'ses-001'})}); e.target.reset(); document.querySelector('#question-status').textContent = 'Question submitted for organizer review.'; notify('Question submitted'); }
    catch (error) { notify(error.message); }
  }
  if (e.target.id === 'feedback-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    const value = name => form.get(name) ? Number(form.get(name)) : null;
    try { await api('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:'ses-001', speaker_rating:value('speaker_rating'), content_rating:value('content_rating'), session_rating:value('content_rating'), comment:form.get('comment')})}); e.target.reset(); notify('Feedback saved — thank you'); }
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
  try { await api('/api/poll-responses', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({poll_id:form.dataset.pollId, answer_text:new FormData(form).get('answer_text')})}); form.reset(); notify('Poll response recorded'); }
  catch (error) { notify(error.message); }
}, true);

document.addEventListener('submit', async e => {
  if (e.target.id === 'capture-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    try {
      await api('/api/capture/text', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({text: form.get('text'), speaker: form.get('speaker'), language: form.get('language')}) });
      state.data = await api('/api/dashboard');
      e.target.reset();
      notify('Live capture saved to the event stream');
    } catch (error) { notify(error.message); }
  }
  if (e.target.id === 'upload-form') {
    e.preventDefault();
    const form = new FormData(e.target);
    try {
      const result = await api('/api/transcription/batch', { method: 'POST', body: form });
      state.data = await api('/api/dashboard');
      notify(`Recording transcribed with ${result.model || 'the configured model'}`);
    } catch (error) { notify(error.message); }
  }
}, true);

// Keep browser Back/Forward inside the single-page workspace.
if (!history.state?.view) history.replaceState({view: state.view}, '', `${location.pathname}${location.search}#landing`);
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

// The public portal URL carries the share token so opening a QR code or an
// embedded WebView loads the same event data and records the portal visit.
connect = function() {
  const d = state.data;
  const token = d.share_links?.[0]?.token || 'gff-live';
  const link = `${location.origin}${location.pathname}?share=${encodeURIComponent(token)}#attendee`;
  const qr = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(link)}`;
  return `${head('Distribution layer', 'Share & connect', 'Give every audience a doorway into the event — on stage, in the app, or in the follow-up email.', '<button class="btn lime" data-action="copy-link">Copy attendee link</button>')}<div class="grid connect-grid"><section class="card share-card"><h3>Attendee portal</h3><p>One clean destination for live takeaways, session details, questions, and post-event content.</p><div class="link-row"><input readonly value="${link}" id="share-link"/><button class="btn ghost" data-action="copy-link">Copy</button></div><div class="qr-wrap"><img class="qr-image" src="${qr}" alt="QR code for attendee portal"/><small>Place this QR on badges, screens, print, and speaker slides.<br><br><strong>${d.share_links?.[0]?.clicks || 0} portal opens</strong> from the current link.</small></div><div class="actions-row"><a class="btn" href="${link}">Open attendee portal ↗</a><button class="btn ghost" data-view-link="screen">Big-screen mode</button></div></section><section class="card share-card"><h3>Connected workflow</h3><p>Keep the event ecosystem moving with simple handoffs.</p><div class="insight"><span class="kind">LIVE DATA</span><strong>Supabase-ready data layer</strong><p>Events, sessions, attendees, insights, and share links are modeled for a direct database connection.</p></div><div class="insight"><span class="kind">EMBED KIT</span><strong>Put it inside your own app</strong><p>Use the portal URL inside an iframe, WebView, QR badge, email CTA, or event app deep link.</p></div><div class="actions-row"><button class="btn ghost" data-view-link="transcript">Live transcript</button><button class="btn ghost" data-action="copy-embed">Copy embed code</button></div></section></div>`;
};

const organizerViews = new Set(['overview', 'capture', 'sessions', 'attendees', 'content', 'connect', 'transcript', 'screen']);
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
    const source = [...(state.data?.transcripts || []), ...(state.data?.insights || [])].map(item => item.text || `${item.title}: ${item.body}`).join('\n') || 'Summarize the key ideas from this event session.';
    if (output) output.innerHTML = '<div class="muted">Generating grounded summary…</div>';
    try {
      const result = await api('/api/ai/summarize', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:source,targetLanguage:'English'})});
      if (output) output.innerHTML = `<div class="insight"><span class="kind">${esc(result.mode || 'AI')} summary</span><p>${esc(result.output)}</p></div>`;
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
    const portal = document.querySelector('#share-link')?.value || `${location.origin}${location.pathname}?share=gff-live#attendee`;
    await navigator.clipboard?.writeText(`<iframe src="${portal}" title="Smart Event Manager attendee portal" loading="lazy"></iframe>`);
    notify('Embed code copied');
  }
  if (action === 'share-social') {
    event.preventDefault();
    event.stopImmediatePropagation();
    const portal = document.querySelector('#share-link')?.value || `${location.origin}${location.pathname}?share=gff-live#attendee`;
    try {
      if (navigator.share) await navigator.share({title:'Smart Event Manager event portal', text:'Join the live event experience', url:portal});
      else await navigator.clipboard?.writeText(portal);
      notify(navigator.share ? 'Share sheet opened' : 'Event link copied for social sharing');
    } catch (_) { notify('Event sharing was cancelled'); }
  }
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
    const source = (state.data?.insights || []).map(item => `${item.title}: ${item.body}`).join('\n') || 'No transcript is available yet. Explain how an event team should prepare useful post-event takeaways.';
    generate.disabled = true;
    try {
      const result = await api('/api/content/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:source,targetLanguage:'English',asset_type:'attendee_recap',title:'Attendee recap'})});
      showGeneratedContent(result);
    } catch (error) { notify(error.message); }
    finally { generate.disabled = false; }
    return;
  }
  if (e.target.closest('[data-action="close-generated"]')) e.target.closest('.generated-output')?.remove();
}, true);
