import httpx
from .config import settings
from .demo_store import demo_store, utc_now

TABLES = {"events", "sessions", "attendees", "insights", "share_links", "transcripts"}


def storage_mode() -> str:
    return "supabase" if settings.supabase_url and settings.supabase_key else "demo"


def _headers() -> dict[str, str]:
    return {"apikey": settings.supabase_key, "Authorization": f"Bearer {settings.supabase_key}"}


def _supabase_list(table: str) -> list[dict]:
    response = httpx.get(f"{settings.supabase_url}/rest/v1/{table}?select=*", headers=_headers(), timeout=20)
    response.raise_for_status()
    return response.json()


def list_items(table: str) -> list[dict]:
    if table not in TABLES:
        raise ValueError(f"Unknown resource: {table}")
    return _supabase_list(table) if storage_mode() == "supabase" else demo_store[table]


def dashboard() -> dict:
    events, sessions, attendees, insights, share_links = [list_items(name) for name in ("events", "sessions", "attendees", "insights", "share_links")]
    return {"event": events[0], "sessions": sessions, "attendees": attendees, "insights": insights, "share_links": share_links, "mode": storage_mode()}


def create_session(payload: dict) -> dict:
    item = {"id": f"ses-{len(demo_store['sessions']) + 1:03d}", "event_id": "evt-001", "title": payload.get("title") or "New session", "track": payload.get("track") or "New track", "room": payload.get("room") or "TBD", "speaker": payload.get("speaker") or "TBD", "starts_at": payload.get("starts_at") or utc_now(), "ends_at": payload.get("ends_at") or utc_now(), "status": "upcoming", "attendance": 0, "sentiment": .8, "summary": "Session brief will appear after the first live signals."}
    demo_store["sessions"].append(item)
    if storage_mode() == "supabase":
        response = httpx.post(f"{settings.supabase_url}/rest/v1/sessions", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=item, timeout=20)
        response.raise_for_status()
        return response.json()[0]
    return item


def create_transcript(payload: dict) -> dict:
    item = {"id": f"trn-{len(demo_store['transcripts']) + 1:03d}", "event_id": payload.get("event_id", "evt-001"), "session_id": payload.get("session_id", "ses-001"), "language": payload.get("language", "auto"), "model": payload.get("model", ""), "text": payload.get("text", ""), "created_at": utc_now()}
    demo_store["transcripts"].insert(0, item)
    if storage_mode() == "supabase":
        response = httpx.post(f"{settings.supabase_url}/rest/v1/transcripts", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=item, timeout=20)
        response.raise_for_status()
        return response.json()[0]
    return item
