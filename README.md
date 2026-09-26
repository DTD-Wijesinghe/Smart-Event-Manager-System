# Smart Event Manager

Local-first event content intelligence workspace inspired by the public SnapSight pattern: capture live conversations, synthesize signal, and remix it into useful event content.

## Structure

```text
smart-event-manager-app/
├─ frontend/               # Browser application and static assets
│  ├─ index.html           # Shell and metadata
│  ├─ app.js               # Frontend entry point
│  ├─ js/app-shell.js      # Frontend feature module and view router
│  ├─ styles.css           # Design system and responsive UI
│  └─ assets/              # Visual assets
├─ backend/                # Backend boundary
│  ├─ config.mjs           # Environment and runtime configuration
│  ├─ demo-store.mjs       # Local demo data
│  ├─ repository.mjs       # Demo/Supabase data access
│  ├─ gemini.mjs           # Gemini API integration
│  └─ http.mjs             # API and static-file routing
├─ supabase/schema.sql     # Database schema and RLS baseline
└─ server.mjs              # Small process entry point
```

## Run locally

```powershell
node server.mjs
```

Then open `http://127.0.0.1:4174/`.

The app runs in demo mode until `SUPABASE_URL` and either `SUPABASE_ANON_KEY` or `SUPABASE_SERVICE_ROLE_KEY` are configured.

## Gemini API integration

This project uses a server-side `GEMINI_API_KEY` for organizer content generation. Create a `.env` file beside `server.mjs` and add:

```env
GEMINI_API_KEY=your-new-gemini-api-key
GEMINI_TEXT_MODEL=gemini-2.5-flash
GEMINI_BATCH_MODEL=gemini-3.5-transcribe
GEMINI_LIVE_MODEL=gemini-3.5-transcribe-live
```

Then run `node server.mjs`. The local server loads `.env` automatically.

The key is never sent to the browser. The endpoints are `POST /api/ai/summarize` with `{ "text": "...", "targetLanguage": "English" }` and `POST /api/transcription/batch` with `{ "data": "BASE64_AUDIO", "mimeType": "audio/webm", "languageCodes": [] }`. Batch audio is limited to 20 MB when sent inline; larger recordings should use the Gemini Files API. A Vertex service-account JSON, LiveKit credentials, and Hugging Face token are not required for this Gemini workflow.

## API surface

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/attendees`
- `GET /api/insights`
- `GET /api/share-links`
