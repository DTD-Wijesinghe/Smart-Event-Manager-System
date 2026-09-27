# Smart Event Manager

Local-first event content intelligence workspace inspired by the public SnapSight pattern: capture live conversations, synthesize signal, and remix it into useful event content.

The backend is FastAPI and uses Vertex AI service-account authentication. The frontend remains a static browser application served by FastAPI.

## Structure

```text
smart-event-manager-app/
├─ frontend/               # Browser application and static assets
│  ├─ index.html           # Shell and metadata
│  ├─ app.js               # Frontend entry point
│  ├─ js/app-shell.js      # Frontend feature module and view router
│  ├─ styles.css           # Design system and responsive UI
│  └─ assets/              # Visual assets
├─ backend/                # FastAPI backend boundary
│  ├─ main.py              # FastAPI app and API routes
│  ├─ config.py             # Environment and runtime configuration
│  ├─ demo_store.py         # Local demo data
│  ├─ repository.py         # Demo data access
│  └─ vertex_ai.py          # Vertex AI / Gemini integration
├─ supabase/schema.sql     # Database schema and RLS baseline
└─ server.mjs              # Small process entry point
```

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://127.0.0.1:8000/`.

## Render deployment

The included `render.yaml` defines the Python web service. Render uses
`pip install -r backend/requirements.txt` to build and
`uvicorn backend.main:app --host 0.0.0.0 --port $PORT` to start it. Add
`GCP_PROJECT`, `VERTEX_SERVICE_ACCOUNT_JSON`, `SUPABASE_URL`, and the
Supabase keys as protected Render environment variables. The Vertex variable
may contain the full service-account JSON; never commit credentials or private
keys.

The app runs in demo mode until `SUPABASE_URL` and either `SUPABASE_ANON_KEY` or `SUPABASE_SERVICE_ROLE_KEY` are configured.

In demo mode, registration and login still use PBKDF2 password hashes and
random bearer sessions; arbitrary passwords are rejected. In Supabase mode,
organizer API requests are checked against the Supabase user session before
the repository is reached. Public attendee share links remain readable.

## Gemini API integration

This project uses Vertex AI service-account authentication. Copy `.env.example` to `.env`, set `VERTEX_SERVICE_ACCOUNT_JSON` to the local credential file, set `GCP_PROJECT`, and set `GCP_LOCATION`. Keep the JSON file out of Git. A typical setup is:

```env
VERTEX_SERVICE_ACCOUNT_JSON=./service-account.json
GCP_PROJECT=your-google-cloud-project
GCP_LOCATION=global
GEMINI_TEXT_MODEL=gemini-2.5-flash
GEMINI_BATCH_MODEL=gemini-3.5-transcribe
GEMINI_LIVE_MODEL=gemini-3.5-transcribe-live
```

Install the packages from `backend/requirements.txt`; the FastAPI app loads `.env` automatically.

The key is never sent to the browser. The endpoints are `POST /api/ai/summarize` with `{ "text": "...", "targetLanguage": "English" }` and `POST /api/transcription/batch` with `{ "data": "BASE64_AUDIO", "mimeType": "audio/webm", "languageCodes": [] }`. Batch audio is limited to 20 MB when sent inline; larger recordings should use the Gemini Files API. A Vertex service-account JSON, LiveKit credentials, and Hugging Face token are not required for this Gemini workflow.

## API surface

- `GET /api/health`
- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/forgot-password`
- `POST /api/auth/logout`
- `POST /api/auth/refresh`
- `POST /api/auth/reset-password` - complete a Supabase recovery-link password update with the recovery bearer token
- `GET /api/dashboard`
- `GET /api/sessions`
- `POST`, `PATCH`, `DELETE /api/sessions` plus `POST /api/sessions/{session_id}/duplicate`
- `POST /api/sessions/{session_id}/start` and `/stop`
- `GET /api/events`
- `GET`, `POST`, `PATCH`, `DELETE /api/speakers` — manage reusable speaker profiles
- `GET`, `POST`, `DELETE /api/sessions/{session_id}/speakers` — assign speaker profiles to sessions
- `POST /api/events`
- `PATCH /api/events/{event_id}`
- `DELETE /api/events/{event_id}`
- `GET /api/attendees`
- `GET /api/insights`
- `GET /api/share-links`
- `GET /api/public/share/{token}` — resolve an attendee link, return its event/session payload, and record a portal open
- The attendee portal is session-aware: attendees can switch sessions, and the selected session scopes its live transcript, Q&A, polls, feedback, and browser capture workflow.
- `GET /api/transcripts?session_id=...` - read the live transcript stream, optionally scoped to a session; the transcript view polls this endpoint every five seconds
- `GET /api/summaries?session_id=...&event_id=...` - read persisted session summaries
- `POST /api/sessions/{session_id}/summary` - generate and persist an evidence-linked session summary
- `GET /api/transcript-segments?session_id=...` - read normalized, time-aware transcript segments for a session
- `PATCH /api/transcript-segments/{segment_id}` - correct a transcript line and save its previous/new text revision
- `GET /api/transcript-segments/{segment_id}/revisions` - read transcript correction history
- `POST /api/transcript-segments/{segment_id}/translate` - translate and persist one segment for a target language
- `GET /api/transcript-segments/{segment_id}/translations` - read saved translations for a segment
- `GET /api/transcripts/export?format=txt|srt|vtt`
- `POST /api/capture/text` - save a live text chunk from a browser, venue bridge, or meeting bot
- `WS /api/ws/capture/{session_id}` - accept live chunks and return persisted transcript/insight events
- `GET /api/questions?session_id=...`
- `POST /api/questions` and `POST /api/questions/{question_id}/votes`
- `GET /api/polls?session_id=...`, `POST /api/polls`, `PATCH /api/polls/{poll_id}`, and `POST /api/poll-responses` — poll results include live option counts and repeated identified votes are rejected
- `POST /api/feedback`
- `GET /api/analytics?event_id=...`
- `GET /api/intelligence?event_id=...` - evidence-linked event-wide themes and cross-session relationships
- `GET /api/search?query=...&event_id=...` - search sessions, transcripts, insights, and generated assets
- `GET /api/topics?event_id=...&session_id=...` - build an evidence-backed topic cloud
- `POST /api/analyst/ask` - answer an event-grounded question with source citations and persist the conversation turn
- `GET /api/analyst/conversations?event_id=...` - list saved analyst conversations
- `GET /api/analyst/conversations/{conversation_id}/messages` - load questions, answers, and evidence citations
- `GET /api/content/assets`
- `GET /api/reports?event_id=...` - list grounded strategic reports for an event
- `POST /api/reports/generate` - generate an evidence-linked report from selected event/session data
- `GET /api/reports/{report_id}/export?format=markdown|json` - download a report
- `POST /api/content/generate` — generate and save a content asset in one request
- `POST /api/ai/summarize`
- `POST /api/ai/translate`
- `POST /api/transcription/batch`
- `POST /api/files/upload` — validate and queue an audio, video, TXT, SRT, or VTT upload for background processing
- `GET /api/files?event_id=...&session_id=...`
- `DELETE /api/files/{file_id}` — remove an uploaded file and its local stored copy
- `GET /api/processing-jobs?event_id=...&session_id=...` — inspect queued/running/completed/failed processing jobs

## Snapsight-style event workflow

1. Capture: connect a venue mixer to the operator laptop, record from the browser microphone, upload a recording, or send live text chunks to `/api/capture/text`.
2. Understand: use batch transcription, live transcript, language selection, and Gemini summaries to turn the source into takeaways.
3. Distribute: show the event QR/link so attendees can open the browser portal without installing an app.
4. Remix: use the Content Studio to prepare executive briefs, attendee recaps, speaker packs, and social-ready moments.
5. Prove value: use sessions, attendee signals, insights, share-link clicks, and reports to show what resonated.

For the Supabase-backed workspace, run `supabase/production_schema.sql` in the Supabase SQL Editor before enabling the service-role variables. The public landing page and demo capture flow remain usable while the database is unavailable. The API lets Supabase generate UUIDs for persisted rows, while the local demo store keeps its readable IDs.

For a ready-to-view workspace after the migration, optionally run `supabase/seed_demo.sql` next. It creates the sample organization, event, session, insight, and attendee portal link without storing credentials.

The expanded production data model is in `supabase/production_schema.sql`. It adds organizations, roles, events, sessions, speakers, transcript segments, translations, takeaways, topics, summaries, analyst conversations and messages, attendees, Q&A, polls, feedback, brand kits, generated assets, processing jobs, and audit logs. Apply it only after reviewing the migration against the current database.

Organizer APIs require a valid session. Public attendee routes remain available from a share link. Mutating organizer routes are role checked: `super_admin`, `organization_admin`, and `event_organizer` can operate the event workspace; `content_editor` can create and edit content/report outputs; speaker and attendee roles are read/public oriented.

AI authentication supports two server-side options: set `GEMINI_API_KEY` for
Gemini Developer API access, or set `GCP_PROJECT` plus
`VERTEX_SERVICE_ACCOUNT_JSON` for Vertex AI. The Gemini key is never sent to
the browser. Render's `render.yaml` declares `GEMINI_API_KEY` as a protected
environment variable; set it in the Render dashboard rather than committing it.
