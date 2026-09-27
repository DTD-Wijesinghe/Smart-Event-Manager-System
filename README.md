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
├─ supabase/production_schema.sql # Current production schema and RLS baseline
├─ supabase/schema.sql     # Legacy minimal schema kept for reference
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
GEMINI_BATCH_MODEL=gemini-3.5-transcribe-preview
GEMINI_LIVE_MODEL=gemini-3.5-transcribe-preview
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
- `POST /api/onboarding/bootstrap` — idempotently create an organizer-owned starter workspace, event, session, and attendee share link after registration
- `GET`, `POST`, `PATCH /api/organizations` — manage organization identity and defaults
- `GET`, `PUT /api/integrations/{provider}` — manage provider connection status without exposing secrets
- `GET /api/admin/overview` — super-admin platform and job health summary
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
- `GET /api/public/assets?event_id=...&session_id=...&share_token=...` — read only published event content through a valid attendee share link
- The attendee portal is session-aware: attendees can switch sessions, and the selected session scopes its live transcript, Q&A, polls, feedback, and browser capture workflow.
- `GET /api/transcripts?session_id=...` - read the live transcript stream, optionally scoped to a session; organizer calls use a bearer session and attendee calls must include the QR `share_token`
- `GET /api/summaries?session_id=...&event_id=...` - read persisted session summaries
- `POST /api/sessions/{session_id}/summary` - generate and persist an evidence-linked session summary
- `GET /api/transcript-segments?session_id=...` - read normalized, time-aware transcript segments for a session
- `PATCH /api/transcript-segments/{segment_id}` - correct a transcript line and save its previous/new text revision
- `GET /api/transcript-segments/{segment_id}/revisions` - read transcript correction history
- `POST /api/transcript-segments/{segment_id}/translate` - translate and persist one segment for a target language
- `GET /api/transcript-segments/{segment_id}/translations` - read saved translations for a segment
- `GET /api/transcripts/export?format=txt|srt|vtt`
- `POST /api/capture/text` - save a live text chunk from a browser, venue bridge, or meeting bot
- `WS /api/ws/capture/{session_id}?token=ACCESS_TOKEN` - accept authenticated JSON text frames or binary `audio/webm` chunks and return persisted transcript/insight events. Browser WebSocket clients can pass the access token in the query string because the WebSocket API does not allow custom Authorization headers; use TLS in production and never log the URL.
- `GET /api/questions?session_id=...`
- `POST /api/questions` and `POST /api/questions/{question_id}/votes`
- `GET /api/polls?session_id=...`, `POST /api/polls`, `PATCH /api/polls/{poll_id}`, and `POST /api/poll-responses` — poll results include live option counts and repeated identified votes are rejected
- `POST /api/feedback`
- `GET /api/analytics?event_id=...`
- `GET /api/intelligence?event_id=...` - evidence-linked event-wide themes and cross-session relationships
- `GET /api/search?query=...&event_id=...&session_id=...&speaker=...&source_type=...&language=...` - search event evidence with metadata filters
- `GET /api/topics?event_id=...&session_id=...` - build an evidence-backed topic cloud
- `POST /api/analyst/ask` - answer an event-grounded question with source citations and persist the conversation turn
- `GET /api/analyst/conversations?event_id=...` - list saved analyst conversations
- `GET /api/analyst/conversations/{conversation_id}/messages` - load questions, answers, and evidence citations
- `GET /api/content/assets`
- `GET /api/content/assets/{asset_id}/export?format=markdown|json|txt|html|pptx|docx` - download a generated content asset; HTML and native PPTX exports are branded slide decks, and DOCX exports are editable documents
- `POST /api/content/assets/{asset_id}/publish` - approve a non-empty content asset for downstream event distribution
- `GET /api/reports?event_id=...` - list grounded strategic reports for an event
- `POST /api/reports/generate` - generate an evidence-linked report from selected event/session data
- `GET /api/reports/{report_id}/export?format=markdown|json` - download a report
- `POST /api/content/generate` — generate and save a content asset in one request
- `POST /api/ai/summarize`
- `POST /api/ai/translate` — organizer-authenticated or valid attendee share-token translation
- `POST /api/transcription/batch`
- `POST /api/files/upload` — validate and queue an audio, video, TXT, SRT, or VTT upload for background processing
- `GET /api/files?event_id=...&session_id=...`
- `DELETE /api/files/{file_id}` — remove an uploaded file and its local stored copy
- `GET /api/processing-jobs?event_id=...&session_id=...` — inspect queued/running/completed/failed processing jobs
- `POST /api/processing-jobs/{job_id}/retry` — requeue a failed stored upload without asking the organizer to upload it again
- `GET /microsite/{event_slug}` — public branded event recap page built from public event data and published content

Public attendee data routes are token-scoped. Event links can read the event's sessions;
session links can read only their assigned session. The token is carried by the QR URL
and is never treated as an organizer credential.

## Snapsight-style event workflow

1. Capture: connect a venue mixer to the operator laptop, record from the browser microphone, upload a recording, or send live text chunks to `/api/capture/text`.
2. Understand: use batch transcription, live transcript, language selection, and Gemini summaries to turn the source into takeaways.
3. Distribute: show the event QR/link so attendees can open the browser portal without installing an app.
4. Remix: use the Content Studio to prepare executive briefs, attendee recaps, speaker packs, and social-ready moments.
5. Publish: preview the branded recap microsite at `/microsite/{event_slug}` and expose only approved content.
6. Prove value: use sessions, attendee signals, insights, share-link clicks, and reports to show what resonated.

For the Supabase-backed workspace, run **`supabase/production_schema.sql`** in the Supabase SQL Editor before enabling the service-role variables. Do not use the legacy `supabase/schema.sql` for a new deployment; it predates the current organizations, auth, content, jobs, and attendee workflows and does not create the tables used by the production API. The public landing page and demo capture flow remain usable while the database is unavailable. The API lets Supabase generate UUIDs for persisted rows, while the local demo store keeps its readable IDs.

Uploaded recordings use the private Supabase Storage bucket `event-media` in deployed mode. The server creates the bucket on the first upload when `SUPABASE_SERVICE_ROLE_KEY` is configured; keep that key server-side and never expose it in frontend code. Local demo mode stores uploads under `data/uploads`. Each upload creates separate `transcription` and `ai_enrichment` processing jobs; the Files screen reports both stages, including grounded local fallback output when an AI provider is unavailable.

For a ready-to-view workspace after the migration, optionally run `supabase/seed_demo.sql` next. It creates the sample organization, event, session, insight, and attendee portal link without storing credentials.

The expanded production data model is in `supabase/production_schema.sql`. It adds organizations, roles, events, sessions, speakers, transcript segments, translations, takeaways, topics, summaries, analyst conversations and messages, attendees, Q&A, polls, feedback, brand kits, generated assets, processing jobs, and audit logs. Apply it only after reviewing the migration against the current database.

Organizer APIs require a valid session. Public attendee routes remain available from a share link. Mutating organizer routes are role checked: `super_admin`, `organization_admin`, and `event_organizer` can operate the event workspace; `content_editor` can create and edit content/report outputs; speaker and attendee roles are read/public oriented. Supabase onboarding additionally requires the server-only `SUPABASE_SERVICE_ROLE_KEY`; it is never exposed to the browser.

AI authentication supports two server-side options: set `GEMINI_API_KEY` for
Gemini Developer API access, or set `GCP_PROJECT` plus
`VERTEX_SERVICE_ACCOUNT_JSON` for Vertex AI. The Gemini key is never sent to
the browser. Render's `render.yaml` declares `GEMINI_API_KEY` as a protected
environment variable; set it in the Render dashboard rather than committing it.

Knowledge retrieval uses a dependency-free local vector ranker by default. To
enable dense Vertex retrieval after billing is enabled for the Google Cloud
project, set `EMBEDDING_PROVIDER=vertex` and optionally set
`GEMINI_EMBEDDING_MODEL` (default `gemini-embedding-001`) and
`GEMINI_EMBEDDING_DIMENSIONS` (default `256`). If Vertex embedding calls fail,
search automatically falls back to the local ranker.
