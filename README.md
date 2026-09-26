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
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://127.0.0.1:8000/`.

The app runs in demo mode until `SUPABASE_URL` and either `SUPABASE_ANON_KEY` or `SUPABASE_SERVICE_ROLE_KEY` are configured.

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
- `GET /api/dashboard`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/attendees`
- `GET /api/insights`
- `GET /api/share-links`
- `GET /api/transcripts`
- `POST /api/capture/text` — save a live text chunk from a browser, venue bridge, or meeting bot
- `POST /api/ai/summarize`
- `POST /api/transcription/batch`

## Snapsight-style event workflow

1. Capture: connect a venue mixer to the operator laptop, upload a recording, or send live text chunks to `/api/capture/text`.
2. Understand: use batch transcription, live transcript, language selection, and Gemini summaries to turn the source into takeaways.
3. Distribute: show the event QR/link so attendees can open the browser portal without installing an app.
4. Remix: use the Content Studio to prepare executive briefs, attendee recaps, speaker packs, and social-ready moments.
5. Prove value: use sessions, attendee signals, insights, share-link clicks, and reports to show what resonated.

For the Supabase-backed workspace, run `supabase/schema.sql` in the Supabase SQL Editor before enabling the service-role variables. The public landing page and demo capture flow remain usable while the database is unavailable.
