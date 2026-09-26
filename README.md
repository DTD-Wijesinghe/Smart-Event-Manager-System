# Smart Event Manager

Local-first event content intelligence workspace inspired by the public SnapSight pattern: capture live conversations, synthesize signal, and remix it into useful event content.

## Structure

```text
smart-event-manager-app/
├─ public/                 # Browser application and static assets
│  ├─ index.html           # Shell and metadata
│  ├─ app.js               # Frontend entry point
│  ├─ js/app-shell.js      # Frontend feature module and view router
│  ├─ styles.css           # Design system and responsive UI
│  └─ assets/              # Visual assets
├─ src/server/             # Backend boundary
│  ├─ config.mjs           # Environment and runtime configuration
│  ├─ demo-store.mjs       # Local demo data
│  ├─ repository.mjs       # Demo/Supabase data access
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

## API surface

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/attendees`
- `GET /api/insights`
- `GET /api/share-links`
