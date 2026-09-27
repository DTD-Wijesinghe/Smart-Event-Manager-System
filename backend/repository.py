import httpx
import re
from .config import settings
from .demo_store import demo_store, utc_now

TABLES = {"organizations", "profiles", "organization_members", "invitations", "brand_kits", "events", "sessions", "speakers", "session_speakers", "attendees", "attendee_preferences", "insights", "share_links", "transcripts", "transcript_segments", "transcript_segment_revisions", "translations", "takeaways", "summaries", "topics", "topic_relations", "ai_conversations", "ai_messages", "questions", "question_votes", "polls", "poll_options", "poll_responses", "feedback", "generated_assets", "generated_asset_versions", "reports", "files", "processing_jobs", "integrations", "audit_logs"}


def storage_mode() -> str:
    return "supabase" if settings.supabase_url and settings.supabase_key else "demo"


def _headers() -> dict[str, str]:
    return {"apikey": settings.supabase_key, "Authorization": f"Bearer {settings.supabase_key}"}


def _supabase_list(table: str) -> list[dict]:
    response = httpx.get(f"{settings.supabase_url}/rest/v1/{table}?select=*", headers=_headers(), timeout=20)
    # A new Supabase project can be connected before its schema is applied.
    # Keep the public workspace usable in that state and fall back to the
    # local demo data until supabase/schema.sql has been run.
    if response.status_code == 404:
        return demo_store[table]
    response.raise_for_status()
    rows = response.json()
    # Keep the original command-center UI useful for a newly created project
    # whose tables exist but have not been seeded yet.
    if not rows and table in {"events", "sessions", "insights", "share_links"}:
        return demo_store[table]
    return [_normalize_row(table, row) for row in rows]


def _normalize_row(table: str, row: dict) -> dict:
    """Keep the original UI contract stable over the normalized schema."""
    if table == "events":
        return {**row, "name": row.get("name") or row.get("title") or "Untitled event"}
    if table == "sessions":
        return {**row, "speaker": row.get("speaker") or "Speaker to be announced", "attendance": row.get("attendance") or 0, "sentiment": row.get("sentiment") if row.get("sentiment") is not None else .8, "summary": row.get("summary") or row.get("description") or "Session brief will appear after the first live signals."}
    return row


def list_items(table: str) -> list[dict]:
    if table not in TABLES:
        raise ValueError(f"Unknown resource: {table}")
    return _supabase_list(table) if storage_mode() == "supabase" else demo_store[table]


def create_file(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"file-{len(demo_store['files']) + 1:04d}", "event_id": event_id, "session_id": session_id, "original_name": payload.get("original_name", "upload"), "storage_path": payload.get("storage_path", ""), "mime_type": payload.get("mime_type", "application/octet-stream"), "size_bytes": int(payload.get("size_bytes") or 0), "status": payload.get("status", "uploaded"), "created_at": utc_now()}
    return _create_item("files", item)


def create_processing_job(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"job-{len(demo_store['processing_jobs']) + 1:04d}", "event_id": event_id, "session_id": session_id, "job_type": payload.get("job_type", "transcription"), "status": payload.get("status", "queued"), "progress": int(payload.get("progress") or 0), "error_message": payload.get("error_message"), "attempts": int(payload.get("attempts") or 0), "created_at": utc_now(), "updated_at": utc_now()}
    return _create_item("processing_jobs", item)


def update_processing_job(job_id: str, **changes: object) -> dict:
    job = next((item for item in list_items("processing_jobs") if item.get("id") == job_id), None)
    if not job:
        raise KeyError("Processing job not found")
    job.update({key: value for key, value in changes.items() if value is not None})
    job["updated_at"] = utc_now()
    if storage_mode() == "supabase" and not str(job_id).startswith("job-"):
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/processing_jobs?id=eq.{job_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json={key: value for key, value in changes.items() if value is not None}, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return rows[0]
    return job


def delete_file(file_id: str) -> dict:
    files = list_items("files")
    removed = next((item for item in files if item.get("id") == file_id), None)
    if removed is None:
        raise KeyError("File not found")
    if str(file_id).startswith("file-"):
        demo_store["files"][:] = [item for item in demo_store["files"] if item.get("id") != file_id]
    if storage_mode() == "supabase" and not str(file_id).startswith("file-"):
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/files?id=eq.{file_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "file": removed}


def attendee_matches(attendee_id: str) -> list[dict]:
    attendees = list_items("attendees")
    target = next((item for item in attendees if item.get("id") == attendee_id), None)
    if not target:
        return []
    target_interests = {str(value).lower() for value in target.get("interests", [])}
    matches = []
    for attendee in attendees:
        if attendee.get("id") == attendee_id:
            continue
        shared = sorted(target_interests & {str(value).lower() for value in attendee.get("interests", [])})
        if not shared:
            continue
        score = min(99, 60 + len(shared) * 12 + round(abs((attendee.get("intent_score") or 0) - (target.get("intent_score") or 0)) * 0.2))
        matches.append({"attendee": attendee, "match_score": score, "shared_interests": shared, "reason": f"Both interested in {', '.join(shared)}"})
    return sorted(matches, key=lambda item: item["match_score"], reverse=True)


def set_attendee_checkin(attendee_id: str, checked_in: bool) -> dict:
    attendee = next((item for item in list_items("attendees") if item.get("id") == attendee_id), None)
    if not attendee:
        raise KeyError("Attendee not found")
    attendee["checked_in"] = checked_in
    if storage_mode() == "supabase" and not str(attendee_id).startswith("att-"):
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/attendees?id=eq.{attendee_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json={"checked_in": checked_in}, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("attendees", rows[0])
    return attendee


def get_share_link(token: str) -> dict | None:
    """Resolve an attendee portal token and increment its public view count."""
    links = list_items("share_links")
    link = next((item for item in links if item.get("token") == token), None)
    if not link:
        return None
    link["clicks"] = int(link.get("clicks") or 0) + 1
    if storage_mode() == "supabase" and link.get("id") and not str(link["id"]).startswith("lnk-"):
        try:
            response = httpx.patch(
                f"{settings.supabase_url}/rest/v1/share_links?id=eq.{link['id']}",
                headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"},
                json={"clicks": link["clicks"]},
                timeout=20,
            )
            if response.is_success and response.json():
                link = _normalize_row("share_links", response.json()[0])
        except httpx.HTTPError:
            pass
    event_id = link.get("event_id")
    events = list_items("events")
    event = next((item for item in events if item.get("id") == event_id), None)
    sessions = [item for item in list_items("sessions") if item.get("event_id") == event_id and (not link.get("session_id") or item.get("id") == link.get("session_id"))] if event else []
    return {"link": link, "event": event or (events[0] if events else None), "sessions": sessions}


def share_link_allows(token: str | None, session_id: str | None = None, event_id: str | None = None) -> bool:
    """Validate a public portal token without incrementing its visit counter."""
    if not token:
        return False
    link = next((item for item in list_items("share_links") if item.get("token") == token), None)
    if not link:
        return False
    if event_id and link.get("event_id") != event_id:
        return False
    if session_id and link.get("session_id") and link.get("session_id") != session_id:
        return False
    return True


def ensure_share_link(event_id: str, session_id: str | None = None) -> dict:
    events = list_items("events")
    event = next((item for item in events if item.get("id") == event_id), None)
    if not event:
        raise KeyError("Event not found")
    existing = next((item for item in list_items("share_links") if item.get("event_id") == event_id and item.get("session_id") == session_id), None)
    if existing:
        return existing
    slug = str(event.get("slug") or event_id).strip().lower()
    suffix = f"-{session_id}" if session_id else "-live"
    token = f"{re.sub(r'[^a-z0-9]+', '-', slug).strip('-')}{suffix}"
    item = {"id": f"lnk-{len(demo_store['share_links']) + 1:03d}", "event_id": event_id, "session_id": session_id, "label": "Session portal" if session_id else "Attendee portal", "token": token, "destination": f"/attendee/{slug}", "clicks": 0, "created_at": utc_now()}
    return _create_item("share_links", item)


def dashboard(event_id: str | None = None) -> dict:
    events, sessions, attendees, insights, share_links, transcripts = [list_items(name) for name in ("events", "sessions", "attendees", "insights", "share_links", "transcripts")]
    event = next((item for item in events if item.get("id") == event_id), None) if event_id else None
    event = event or (events[0] if events else None)
    selected_id = event.get("id", "evt-001") if event else (event_id or "evt-001")
    scoped = lambda rows: [item for item in rows if not item.get("event_id") or item.get("event_id") == selected_id]
    selected_links = scoped(share_links)
    if event:
        selected_links = [ensure_share_link(selected_id)]
    return {"event": event, "sessions": scoped(sessions), "attendees": scoped(attendees), "insights": scoped(insights), "share_links": selected_links, "transcripts": scoped(transcripts), "analytics": analytics(selected_id), "mode": storage_mode()}


def _remote_insert(table: str, item: dict) -> dict | None:
    """Insert a row while letting Supabase generate UUID primary keys."""
    # The dashboard may temporarily be rendering local demo rows while a new
    # Supabase project has no seed data. Never send those readable demo IDs to
    # UUID foreign-key columns; keep the action local until real rows exist.
    foreign_keys = ("organization_id", "event_id", "session_id", "poll_id", "option_id", "asset_id", "speaker_id", "conversation_id")
    if any(str(item.get(key, "")).startswith(("org-", "evt-", "ses-", "poll-", "q-", "asset-", "speaker-")) for key in foreign_keys if item.get(key)):
        return None
    payload = {key: value for key, value in item.items() if key != "id"}
    response = httpx.post(f"{settings.supabase_url}/rest/v1/{table}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=payload, timeout=20)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else item


def _default_foreign_keys(payload: dict) -> tuple[str, str]:
    events = list_items("events")
    sessions = list_items("sessions")
    event_id = payload.get("event_id") or (events[0].get("id") if events else "evt-001")
    session_id = payload.get("session_id") or (sessions[0].get("id") if sessions else "ses-001")
    return event_id, session_id


def create_session(payload: dict) -> dict:
    event_id, _ = _default_foreign_keys(payload)
    item = {"id": f"ses-{len(demo_store['sessions']) + 1:03d}", "event_id": event_id, "title": payload.get("title") or "New session", "track": payload.get("track") or "New track", "room": payload.get("room") or "TBD", "speaker": payload.get("speaker") or "TBD", "starts_at": payload.get("starts_at") or utc_now(), "ends_at": payload.get("ends_at") or utc_now(), "status": "upcoming", "attendance": 0, "sentiment": .8, "summary": "Session brief will appear after the first live signals."}
    demo_store["sessions"].append(item)
    if storage_mode() == "supabase":
        remote_item = {key: item[key] for key in ("event_id", "title", "starts_at", "ends_at", "room", "track") if item.get(key) is not None}
        remote_item["status"] = "scheduled"
        remote_item["description"] = item.get("summary", "")
        return _remote_insert("sessions", remote_item) or item
    return item


def update_session(session_id: str, payload: dict) -> dict:
    session = next((item for item in list_items("sessions") if item.get("id") == session_id), None)
    if not session:
        raise KeyError("Session not found")
    session.update({key: value for key, value in payload.items() if value is not None})
    if storage_mode() == "supabase" and not str(session_id).startswith("ses-"):
        remote = {key: value for key, value in payload.items() if value is not None and key not in {"id", "speaker", "attendance", "sentiment", "status"}}
        if "status" in payload:
            remote["status"] = "scheduled" if payload["status"] == "upcoming" else payload["status"]
        if "summary" in payload:
            remote["description"] = payload["summary"]
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/sessions?id=eq.{session_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("sessions", rows[0])
    return session


def delete_session(session_id: str) -> dict:
    session = next((item for item in list_items("sessions") if item.get("id") == session_id), None)
    if not session:
        raise KeyError("Session not found")
    removed = session
    if str(session_id).startswith("ses-"):
        demo_store["sessions"][:] = [item for item in demo_store["sessions"] if item.get("id") != session_id]
    if storage_mode() == "supabase" and not str(session_id).startswith("ses-"):
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/sessions?id=eq.{session_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "session": removed}


def duplicate_session(session_id: str) -> dict:
    source = next((item for item in list_items("sessions") if item.get("id") == session_id), None)
    if not source:
        raise KeyError("Session not found")
    return create_session({"event_id": source.get("event_id"), "title": f"{source.get('title', 'Session')} (copy)", "track": source.get("track"), "room": source.get("room"), "speaker": source.get("speaker"), "starts_at": source.get("starts_at"), "ends_at": source.get("ends_at")})


def set_session_status(session_id: str, status: str) -> dict:
    if status not in {"live", "completed", "scheduled", "upcoming", "archived"}:
        raise ValueError("Unsupported session status")
    return update_session(session_id, {"status": status})


def create_transcript(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"trn-{len(demo_store['transcripts']) + 1:03d}", "event_id": event_id, "session_id": session_id, "language": payload.get("language", "auto"), "model": payload.get("model", ""), "text": payload.get("text", ""), "speaker": payload.get("speaker", "Live speaker"), "created_at": utc_now()}
    demo_store["transcripts"].insert(0, item)
    segment = {"id": f"seg-{len(demo_store['transcript_segments']) + 1:04d}", "session_id": session_id, "speaker": payload.get("speaker", "Live speaker"), "text": payload.get("text", ""), "language": payload.get("language", "auto"), "start_time_ms": payload.get("start_time_ms"), "end_time_ms": payload.get("end_time_ms"), "confidence": payload.get("confidence"), "source": payload.get("source", "live"), "created_at": item["created_at"]}
    item["segment_id"] = segment["id"]
    demo_store["transcript_segments"].insert(0, segment)
    if storage_mode() == "supabase":
        saved = _remote_insert("transcripts", {key: value for key, value in item.items() if key != "segment_id"}) or item
        _remote_insert("transcript_segments", {key: value for key, value in segment.items() if key != "speaker"})
        return saved
    return {**item, "segment_id": segment["id"]}


def list_transcript_segments(session_id: str | None = None) -> list[dict]:
    return [item for item in list_items("transcript_segments") if not session_id or item.get("session_id") == session_id]


def update_transcript_segment(segment_id: str, text: str, editor: str = "organizer") -> dict:
    segment = next((item for item in list_items("transcript_segments") if item.get("id") == segment_id), None)
    if not segment:
        raise KeyError("Transcript segment not found")
    text = text.strip()
    if not text:
        raise ValueError("Transcript text is required")
    revision = {"id": f"segment-revision-{len(demo_store['transcript_segment_revisions']) + 1:04d}", "transcript_segment_id": segment_id, "previous_text": segment.get("text", ""), "new_text": text, "edited_by": editor, "created_at": utc_now()}
    demo_store["transcript_segment_revisions"].insert(0, revision)
    segment["text"] = text
    compatibility = next((row for row in demo_store["transcripts"] if row.get("segment_id") == segment_id), None)
    if compatibility:
        compatibility["text"] = text
    if storage_mode() == "supabase" and not str(segment_id).startswith("seg-"):
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/transcript_segments?id=eq.{segment_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json={"text": text}, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                segment = {**segment, **rows[0]}
        _remote_insert("transcript_segment_revisions", {key: value for key, value in revision.items() if key != "id"})
    return {"segment": segment, "revision": revision}


def list_transcript_segment_revisions(segment_id: str) -> list[dict]:
    return [item for item in list_items("transcript_segment_revisions") if item.get("transcript_segment_id") == segment_id]


def create_translation(payload: dict) -> dict:
    segment_id = payload.get("transcript_segment_id")
    item = {"id": f"translation-{len(demo_store['translations']) + 1:04d}", "transcript_segment_id": segment_id, "language": payload.get("language", "English"), "text": payload.get("text", ""), "created_at": utc_now()}
    existing = next((row for row in list_items("translations") if row.get("transcript_segment_id") == segment_id and row.get("language") == item["language"]), None)
    if existing:
        existing.update({"text": item["text"], "created_at": item["created_at"]})
        return existing
    return _create_item("translations", item)


def create_insight(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    source = payload.get("text", "").strip()
    first_sentence = source.split(".", 1)[0].strip() or "New live signal"
    item = {"id": f"ins-live-{len(demo_store['insights']) + 1:03d}", "event_id": event_id, "session_id": session_id, "kind": "live signal", "title": first_sentence[:96], "body": f"{payload.get('speaker', 'Live speaker')}: {source[:320]}", "confidence": .82, "created_at": utc_now()}
    return _create_item("insights", item)


def _create_item(table: str, item: dict) -> dict:
    demo_store[table].insert(0, item)
    if storage_mode() == "supabase":
        if table == "events":
            if not item.get("organization_id") or str(item.get("organization_id")).startswith("org-"): return item
            remote_item = {key: value for key, value in item.items() if key not in {"id", "name"}}
            remote_item["title"] = item.get("name") or item.get("title") or "New event"
            return _remote_insert("events", remote_item) or item
        if table == "invitations":
            remote_item = {key: item[key] for key in ("organization_id", "email", "role", "status", "token") if item.get(key) is not None}
            response = httpx.post(f"{settings.supabase_url}/rest/v1/invitations", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            rows = response.json()
            return _normalize_row("invitations", rows[0]) if rows else item
        if table == "sessions":
            remote_item = {key: item[key] for key in ("event_id", "title", "starts_at", "ends_at", "room", "track") if item.get(key) is not None}
            remote_item["status"] = "scheduled" if item.get("status") == "upcoming" else item.get("status", "scheduled")
            remote_item["description"] = item.get("summary", "")
            if str(remote_item.get("event_id", "")).startswith("evt-"): return item
            return _remote_insert("sessions", remote_item) or item
        if table == "questions":
            remote_item = {key: item[key] for key in ("session_id", "body", "anonymous", "status", "pinned") if key in item}
            if str(remote_item.get("session_id", "")).startswith("ses-"): return item
            response = httpx.post(f"{settings.supabase_url}/rest/v1/questions", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            return response.json()[0]
        if table == "poll_options":
            remote_item = {key: item[key] for key in ("poll_id", "label", "sort_order") if key in item}
            if str(remote_item.get("poll_id", "")).startswith("poll-"): return item
            response = httpx.post(f"{settings.supabase_url}/rest/v1/poll_options", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            return response.json()[0]
        if table == "poll_responses":
            remote_item = {key: item[key] for key in ("poll_id", "option_id", "answer_text", "rating") if item.get(key) is not None}
            if str(remote_item.get("poll_id", "")).startswith("poll-"): return item
            response = httpx.post(f"{settings.supabase_url}/rest/v1/poll_responses", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            return response.json()[0]
        if table == "feedback":
            remote_item = {key: item[key] for key in ("session_id", "speaker_rating", "content_rating", "session_rating", "comment") if item.get(key) is not None}
            if str(remote_item.get("session_id", "")).startswith("ses-"): return item
            response = httpx.post(f"{settings.supabase_url}/rest/v1/feedback", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            return response.json()[0]
        if table == "generated_assets":
            remote_item = {key: item[key] for key in ("event_id", "asset_type", "title", "content", "status") if item.get(key) is not None}
            if str(remote_item.get("event_id", "")).startswith("evt-"): return item
            response = httpx.post(f"{settings.supabase_url}/rest/v1/generated_assets", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote_item, timeout=20)
            if response.status_code == 404: return item
            response.raise_for_status()
            return response.json()[0]
        return _remote_insert(table, item) or item
    return item


def create_event(payload: dict) -> dict:
    number = len(demo_store["events"]) + 1
    organization_id = payload.get("organization_id") or next((item.get("id") for item in list_items("organizations")), "org-demo")
    item = {"id": f"evt-{number:03d}", "organization_id": organization_id, "name": payload.get("name") or payload.get("title") or "New event", "slug": payload.get("slug") or f"event-{number:03d}", "venue": payload.get("venue", ""), "starts_at": payload.get("starts_at") or utc_now(), "ends_at": payload.get("ends_at") or utc_now(), "status": payload.get("status", "draft"), "brand_color": payload.get("brand_color", "#7568f3")}
    return _create_item("events", item)


def create_speaker(payload: dict) -> dict:
    organization_id = payload.get("organization_id") or next((item.get("id") for item in list_items("organizations")), "org-demo")
    name = str(payload.get("name", "")).strip()
    if len(name) < 2:
        raise ValueError("Speaker name is required")
    item = {"id": f"speaker-{len(demo_store['speakers']) + 1:03d}", "organization_id": organization_id, "name": name, "title": str(payload.get("title", "")).strip(), "company": str(payload.get("company", "")).strip(), "biography": str(payload.get("biography", "")).strip(), "photo_url": str(payload.get("photo_url", "")).strip(), "profile_url": str(payload.get("profile_url", "")).strip(), "created_at": utc_now()}
    return _create_item("speakers", item)


def update_speaker(speaker_id: str, payload: dict) -> dict:
    speaker = next((item for item in list_items("speakers") if item.get("id") == speaker_id), None)
    if not speaker:
        raise KeyError("Speaker not found")
    speaker.update({key: value for key, value in payload.items() if value is not None})
    if storage_mode() == "supabase" and not str(speaker_id).startswith("speaker-"):
        fields = {key: value for key, value in payload.items() if key in {"name", "title", "company", "biography", "photo_url", "profile_url"} and value is not None}
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/speakers?id=eq.{speaker_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=fields, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return rows[0]
    return speaker


def delete_speaker(speaker_id: str) -> dict:
    speaker = next((item for item in list_items("speakers") if item.get("id") == speaker_id), None)
    if not speaker:
        raise KeyError("Speaker not found")
    if str(speaker_id).startswith("speaker-"):
        demo_store["speakers"][:] = [item for item in demo_store["speakers"] if item.get("id") != speaker_id]
        demo_store["session_speakers"][:] = [item for item in demo_store["session_speakers"] if item.get("speaker_id") != speaker_id]
    elif storage_mode() == "supabase":
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/speakers?id=eq.{speaker_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "speaker": speaker}


def list_session_speakers(session_id: str) -> list[dict]:
    relations = [item for item in list_items("session_speakers") if item.get("session_id") == session_id]
    speakers = {item.get("id"): item for item in list_items("speakers")}
    return [{**speakers[relation.get("speaker_id")], "sort_order": relation.get("sort_order", 0)} for relation in relations if relation.get("speaker_id") in speakers]


def assign_session_speaker(session_id: str, speaker_id: str, sort_order: int = 0) -> dict:
    if not any(item.get("id") == session_id for item in list_items("sessions")):
        raise KeyError("Session not found")
    if not any(item.get("id") == speaker_id for item in list_items("speakers")):
        raise KeyError("Speaker not found")
    if any(item.get("session_id") == session_id and item.get("speaker_id") == speaker_id for item in list_items("session_speakers")):
        return {"session_id": session_id, "speaker_id": speaker_id, "sort_order": sort_order}
    return _create_item("session_speakers", {"id": f"session-speaker-{len(demo_store['session_speakers']) + 1:04d}", "session_id": session_id, "speaker_id": speaker_id, "sort_order": sort_order})


def unassign_session_speaker(session_id: str, speaker_id: str) -> dict:
    relation = next((item for item in list_items("session_speakers") if item.get("session_id") == session_id and item.get("speaker_id") == speaker_id), None)
    if not relation:
        raise KeyError("Speaker is not assigned to this session")
    if str(relation.get("id", "")).startswith("session-speaker-"):
        demo_store["session_speakers"][:] = [item for item in demo_store["session_speakers"] if item.get("id") != relation.get("id")]
    elif storage_mode() == "supabase":
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/session_speakers?session_id=eq.{session_id}&speaker_id=eq.{speaker_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "session_id": session_id, "speaker_id": speaker_id}


def team_workspace(organization_id: str | None = None) -> dict:
    organization_id = organization_id or "org-demo"
    members = [item for item in list_items("organization_members") if item.get("organization_id") == organization_id]
    invitations = [item for item in list_items("invitations") if item.get("organization_id") == organization_id]
    if storage_mode() == "supabase" and not members:
        members = demo_store["organization_members"] if organization_id == "org-demo" else []
    return {"organization_id": organization_id, "members": members, "invitations": invitations}


def list_organizations() -> list[dict]:
    return list_items("organizations")


def create_organization(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    if len(name) < 2:
        raise ValueError("Organization name must contain at least two characters")
    slug = re.sub(r"[^a-z0-9]+", "-", str(payload.get("slug") or name).lower()).strip("-")
    if not slug:
        raise ValueError("Organization slug is required")
    if any(str(item.get("slug")) == slug for item in list_items("organizations")):
        raise ValueError("Organization slug is already in use")
    item = {"id": f"org-{len(demo_store['organizations']) + 1:03d}", "name": name, "slug": slug, "logo_url": str(payload.get("logo_url") or "").strip(), "website": str(payload.get("website") or "").strip(), "industry": str(payload.get("industry") or "").strip(), "timezone": str(payload.get("timezone") or "UTC"), "preferred_language": str(payload.get("preferred_language") or "en"), "created_at": utc_now(), "updated_at": utc_now()}
    return _create_item("organizations", item)


def update_organization(organization_id: str, payload: dict) -> dict:
    organization = next((item for item in list_items("organizations") if item.get("id") == organization_id), None)
    if not organization:
        raise KeyError("Organization not found")
    allowed = {"name", "slug", "logo_url", "website", "industry", "timezone", "preferred_language"}
    changes = {key: str(value).strip() for key, value in payload.items() if key in allowed and value is not None}
    if "name" in changes and len(changes["name"]) < 2:
        raise ValueError("Organization name must contain at least two characters")
    if "slug" in changes:
        changes["slug"] = re.sub(r"[^a-z0-9]+", "-", changes["slug"].lower()).strip("-")
        if any(item.get("id") != organization_id and item.get("slug") == changes["slug"] for item in list_items("organizations")):
            raise ValueError("Organization slug is already in use")
    organization.update(changes)
    organization["updated_at"] = utc_now()
    if storage_mode() == "supabase" and not str(organization_id).startswith("org-"):
        remote = {key: value for key, value in changes.items() if key != "name"}
        if "name" in changes:
            remote["name"] = changes["name"]
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/organizations?id=eq.{organization_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return rows[0]
    return organization


def list_integrations(organization_id: str | None = None) -> list[dict]:
    rows = list_items("integrations")
    return [row for row in rows if not organization_id or row.get("organization_id") == organization_id]


def upsert_integration(payload: dict) -> dict:
    organization_id = payload.get("organization_id") or "org-demo"
    provider = str(payload.get("provider") or "").strip().lower()
    if not provider or len(provider) > 80:
        raise ValueError("Integration provider is required")
    allowed_statuses = {"connected", "disconnected", "error", "pending"}
    status = str(payload.get("status") or "disconnected").lower()
    if status not in allowed_statuses:
        raise ValueError("Unsupported integration status")
    existing = next((row for row in list_items("integrations") if row.get("organization_id") == organization_id and row.get("provider") == provider), None)
    values = {"provider": provider, "status": status, "display_name": str(payload.get("display_name") or provider.title()).strip(), "metadata": payload.get("metadata") or {}, "updated_at": utc_now()}
    if existing:
        existing.update(values)
        if storage_mode() == "supabase" and existing.get("id") and not str(existing["id"]).startswith("integration-"):
            response = httpx.patch(f"{settings.supabase_url}/rest/v1/integrations?id=eq.{existing['id']}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=values, timeout=20)
            if response.status_code != 404:
                response.raise_for_status()
                rows = response.json()
                if rows:
                    return rows[0]
        return existing
    return _create_item("integrations", {"id": f"integration-{len(demo_store['integrations']) + 1:04d}", "organization_id": organization_id, "created_at": utc_now(), **values})


def admin_overview() -> dict:
    jobs = list_items("processing_jobs")
    return {"organizations": len(list_items("organizations")), "users": len(list_items("profiles")), "events": len(list_items("events")), "sessions": len(list_items("sessions")), "storage_files": len(list_items("files")), "ai_usage": {"generated_assets": len(list_items("generated_assets")), "analyst_messages": len(list_items("ai_messages"))}, "jobs": {"queued": sum(row.get("status") == "queued" for row in jobs), "running": sum(row.get("status") == "running" for row in jobs), "failed": sum(row.get("status") == "failed" for row in jobs), "completed": sum(row.get("status") == "completed" for row in jobs)}, "api": {"storage_mode": storage_mode(), "ai_configured": bool(settings.project or settings.gemini_api_key)}}


def get_brand_kit(organization_id: str | None = None) -> dict:
    organization_id = organization_id or "org-demo"
    kit = next((item for item in list_items("brand_kits") if item.get("organization_id") == organization_id), None)
    return kit or {"id": None, "organization_id": organization_id, "name": "Default brand", "logo_url": "", "primary_color": "#7568f3", "secondary_color": "#1b1c2d", "accent_color": "#e4ff63", "font_family": "Inter", "tone": "clear, generous, modern", "website": ""}


def upsert_brand_kit(payload: dict) -> dict:
    organization_id = payload.get("organization_id") or "org-demo"
    kit = get_brand_kit(organization_id)
    fields = ("name", "logo_url", "primary_color", "secondary_color", "accent_color", "font_family", "tone", "website")
    values = {key: str(payload.get(key, kit.get(key, ""))).strip() for key in fields}
    if kit.get("id"):
        kit.update(values)
        if storage_mode() == "supabase" and not str(kit["id"]).startswith("brand-"):
            response = httpx.patch(f"{settings.supabase_url}/rest/v1/brand_kits?id=eq.{kit['id']}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=values, timeout=20)
            if response.status_code != 404:
                response.raise_for_status()
                rows = response.json()
                if rows: return rows[0]
        return kit
    return _create_item("brand_kits", {"id": f"brand-{len(demo_store['brand_kits']) + 1:03d}", "organization_id": organization_id, **values, "created_at": utc_now()})


def create_invitation(payload: dict) -> dict:
    allowed_roles = {"organization_admin", "event_organizer", "content_editor", "speaker", "attendee"}
    role = payload.get("role", "event_organizer")
    if role not in allowed_roles:
        raise ValueError("Unsupported team role")
    email = str(payload.get("email", "")).strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid invite email is required")
    organization_id = payload.get("organization_id") or "org-demo"
    item = {"id": f"invite-{len(demo_store['invitations']) + 1:03d}", "organization_id": organization_id, "email": email, "role": role, "status": "invited", "token": f"invite-token-{len(demo_store['invitations']) + 1:03d}", "created_at": utc_now()}
    return _create_item("invitations", item)


def update_team_member(member_id: str, role: str | None = None, status: str | None = None) -> dict:
    allowed_roles = {"organization_admin", "event_organizer", "content_editor", "speaker", "attendee"}
    allowed_statuses = {"active", "invited", "suspended"}
    if role is not None and role not in allowed_roles:
        raise ValueError("Unsupported team role")
    if status is not None and status not in allowed_statuses:
        raise ValueError("Unsupported member status")
    member = next((item for item in list_items("organization_members") if item.get("id") == member_id or item.get("user_id") == member_id), None)
    if not member:
        raise KeyError("Team member not found")
    if role is not None: member["role"] = role
    if status is not None: member["status"] = status
    if storage_mode() == "supabase" and member.get("organization_id") and member.get("user_id"):
        remote = {key: value for key, value in (("role", role), ("status", status)) if value is not None}
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/organization_members?organization_id=eq.{member['organization_id']}&user_id=eq.{member['user_id']}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows: return _normalize_row("organization_members", rows[0])
    return member


def delete_event(event_id: str) -> dict:
    event = next((item for item in list_items("events") if item.get("id") == event_id), None)
    if not event:
        raise KeyError("Event not found")
    removed = event
    if str(event_id).startswith("evt-"):
        demo_store["events"][:] = [item for item in demo_store["events"] if item.get("id") != event_id]
        demo_store["sessions"][:] = [item for item in demo_store["sessions"] if item.get("event_id") != event_id]
    if storage_mode() == "supabase" and not str(event_id).startswith("evt-"):
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/events?id=eq.{event_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "event": removed}


def update_event(event_id: str, payload: dict) -> dict:
    event = next((item for item in list_items("events") if item.get("id") == event_id), None)
    if not event:
        raise KeyError("Event not found")
    event.update({key: value for key, value in payload.items() if value is not None})
    if storage_mode() == "supabase" and event_id and not str(event_id).startswith("evt-"):
        remote = {key: value for key, value in payload.items() if value is not None and key not in {"id", "name"}}
        if "name" in payload:
            remote["title"] = payload["name"]
        response = httpx.patch(
            f"{settings.supabase_url}/rest/v1/events?id=eq.{event_id}",
            headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"},
            json=remote,
            timeout=20,
        )
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("events", rows[0])
    return event


def create_question(payload: dict) -> dict:
    _, session_id = _default_foreign_keys(payload)
    item = {"id": f"q-{len(demo_store['questions']) + 1:03d}", "session_id": session_id, "body": payload["body"].strip(), "anonymous": bool(payload.get("anonymous", False)), "status": "pending", "pinned": False, "votes": 0, "created_at": utc_now()}
    return _create_item("questions", item)


def vote_question(question_id: str, voter_id: str = "anonymous") -> dict:
    question = next((item for item in list_items("questions") if item.get("id") == question_id), None)
    if not question:
        raise KeyError("Question not found")
    vote_key = f"{question_id}:{voter_id}"
    if not any(item["id"] == vote_key for item in demo_store["question_votes"]):
        demo_store["question_votes"].append({"id": vote_key, "question_id": question_id, "voter_id": voter_id, "created_at": utc_now()})
        question["votes"] = question.get("votes", 0) + 1
    return question


def moderate_question(question_id: str, status: str | None = None, pinned: bool | None = None) -> dict:
    if status is not None and status not in {"pending", "approved", "answered", "dismissed"}:
        raise ValueError("Unsupported question status")
    question = next((item for item in list_items("questions") if item.get("id") == question_id), None)
    if not question:
        raise KeyError("Question not found")
    if status is not None:
        question["status"] = status
    if pinned is not None:
        question["pinned"] = pinned
    if storage_mode() == "supabase" and not str(question_id).startswith("q-"):
        remote = {key: value for key, value in (("status", status), ("pinned", pinned)) if value is not None}
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/questions?id=eq.{question_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json=remote, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("questions", rows[0])
    return question


def create_poll(payload: dict) -> dict:
    number = len(demo_store["polls"]) + 1
    _, session_id = _default_foreign_keys(payload)
    item = {"id": f"poll-{number:03d}", "session_id": session_id, "question": payload["question"].strip(), "poll_type": payload.get("poll_type", "single"), "is_open": False, "created_at": utc_now()}
    saved = _create_item("polls", item)
    for index, label in enumerate(payload.get("options", [])):
        _create_item("poll_options", {"id": f"{saved['id']}-opt-{index + 1}", "poll_id": saved["id"], "label": label, "sort_order": index})
    return saved


def update_poll(poll_id: str, is_open: bool | None = None) -> dict:
    if is_open is None:
        raise ValueError("is_open is required")
    polls = list_items("polls")
    poll = next((item for item in polls if item.get("id") == poll_id), None)
    if not poll:
        raise KeyError("Poll not found")
    poll["is_open"] = bool(is_open)
    if storage_mode() == "supabase" and poll_id and not str(poll_id).startswith("poll-"):
        response = httpx.patch(
            f"{settings.supabase_url}/rest/v1/polls?id=eq.{poll_id}",
            headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"},
            json={"is_open": bool(is_open)},
            timeout=20,
        )
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("polls", rows[0])
    return poll


def respond_poll(payload: dict) -> dict:
    item = {"id": f"response-{len(demo_store['poll_responses']) + 1:03d}", "poll_id": payload["poll_id"], "option_id": payload.get("option_id"), "attendee_id": payload.get("attendee_id", "anonymous"), "answer_text": payload.get("answer_text"), "rating": payload.get("rating"), "created_at": utc_now()}
    return _create_item("poll_responses", item)


def create_feedback(payload: dict) -> dict:
    item = {"id": f"feedback-{len(demo_store['feedback']) + 1:03d}", "session_id": payload.get("session_id", "ses-001"), "speaker_rating": payload.get("speaker_rating"), "content_rating": payload.get("content_rating"), "session_rating": payload.get("session_rating"), "comment": payload.get("comment", ""), "created_at": utc_now()}
    return _create_item("feedback", item)


def create_generated_asset(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"asset-{len(demo_store['generated_assets']) + 1:03d}", "event_id": event_id, "session_id": session_id, "asset_type": payload.get("asset_type", "attendee_recap"), "title": payload.get("title", "Generated event asset"), "content": payload.get("content", {}), "status": payload.get("status", "draft"), "created_at": utc_now()}
    saved = _create_item("generated_assets", item)
    create_asset_version({"asset_id": saved.get("id"), "title": saved.get("title"), "content": saved.get("content", {}), "version": 1})
    return saved


def create_asset_version(payload: dict) -> dict:
    asset_id = payload.get("asset_id")
    versions = [item for item in list_items("generated_asset_versions") if item.get("asset_id") == asset_id]
    version = int(payload.get("version") or (max([int(item.get("version") or 0) for item in versions], default=0) + 1))
    item = {"id": f"asset-version-{len(demo_store['generated_asset_versions']) + 1:04d}", "asset_id": asset_id, "version": version, "title": payload.get("title", "Generated asset"), "content": payload.get("content", {}), "created_at": utc_now()}
    return _create_item("generated_asset_versions", item)


def list_asset_versions(asset_id: str) -> list[dict]:
    return sorted([item for item in list_items("generated_asset_versions") if item.get("asset_id") == asset_id], key=lambda item: int(item.get("version") or 0), reverse=True)


def update_generated_asset(asset_id: str, payload: dict) -> dict:
    asset = next((item for item in list_items("generated_assets") if item.get("id") == asset_id), None)
    if not asset:
        raise KeyError("Generated asset not found")
    title = payload.get("title") if payload.get("title") is not None else asset.get("title")
    content = payload.get("content") if payload.get("content") is not None else asset.get("content", {})
    create_asset_version({"asset_id": asset_id, "title": title, "content": content})
    asset.update({"title": title, "content": content, "status": payload.get("status") or asset.get("status", "draft"), "updated_at": utc_now()})
    if storage_mode() == "supabase" and not str(asset_id).startswith("asset-"):
        response = httpx.patch(f"{settings.supabase_url}/rest/v1/generated_assets?id=eq.{asset_id}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json={"title": title, "content": content, "status": asset.get("status")}, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return rows[0]
    return asset


def create_takeaway(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    evidence = payload.get("evidence") or []
    item = {"id": f"takeaway-{len(demo_store['takeaways']) + 1:03d}", "event_id": event_id, "session_id": session_id, "title": payload.get("title") or "Event takeaway", "body": payload.get("body", "").strip(), "confidence": payload.get("confidence", .85), "evidence": evidence, "created_at": utc_now()}
    return _create_item("takeaways", item)


def create_report(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    source_session_ids = payload.get("source_session_ids") or ([session_id] if session_id else [])
    item = {"id": f"report-{len(demo_store['reports']) + 1:04d}", "event_id": event_id, "title": payload.get("title") or "Event intelligence report", "report_type": payload.get("report_type", "executive_brief"), "format": payload.get("format", "markdown"), "source_session_ids": source_session_ids, "content": payload.get("content") or {}, "status": payload.get("status", "completed"), "created_at": utc_now(), "updated_at": utc_now()}
    return _create_item("reports", item)


def list_reports(event_id: str | None = None) -> list[dict]:
    return [item for item in list_items("reports") if not event_id or item.get("event_id") == event_id]


def save_summary(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"summary-{len(demo_store['summaries']) + 1:04d}", "event_id": event_id, "session_id": session_id, "language": payload.get("language", "English"), "content": payload.get("content") or {}, "model": payload.get("model", "local-grounded"), "created_at": utc_now(), "updated_at": utc_now()}
    existing = next((row for row in list_items("summaries") if row.get("session_id") == session_id and row.get("language") == item["language"]), None)
    if existing:
        existing.update({"content": item["content"], "model": item["model"], "updated_at": item["updated_at"]})
        if storage_mode() == "supabase" and not str(existing.get("id", "")).startswith("summary-"):
            response = httpx.patch(f"{settings.supabase_url}/rest/v1/summaries?id=eq.{existing['id']}", headers={**_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}, json={"content": item["content"], "model": item["model"], "updated_at": item["updated_at"]}, timeout=20)
            if response.status_code != 404:
                response.raise_for_status()
                rows = response.json()
                if rows:
                    return _normalize_row("summaries", rows[0])
        return existing
    if storage_mode() == "supabase" and not str(event_id).startswith("evt-") and not str(session_id).startswith("ses-"):
        demo_store["summaries"].insert(0, item)
        remote = {key: value for key, value in item.items() if key != "id"}
        response = httpx.post(f"{settings.supabase_url}/rest/v1/summaries?on_conflict=session_id,language", headers={**_headers(), "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=representation"}, json=remote, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            rows = response.json()
            if rows:
                return _normalize_row("summaries", rows[0])
        return item
    return _create_item("summaries", item)


def list_summaries(session_id: str | None = None, event_id: str | None = None) -> list[dict]:
    return [item for item in list_items("summaries") if (not session_id or item.get("session_id") == session_id) and (not event_id or item.get("event_id") == event_id)]


def create_ai_conversation(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"conv-{len(demo_store['ai_conversations']) + 1:04d}", "event_id": event_id, "session_id": payload.get("session_id") or session_id, "user_id": payload.get("user_id"), "title": str(payload.get("title") or "Event analyst conversation").strip()[:160], "created_at": utc_now(), "updated_at": utc_now()}
    return _create_item("ai_conversations", item)


def list_ai_conversations(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    rows = list_items("ai_conversations")
    return sorted([row for row in rows if (not event_id or row.get("event_id") == event_id) and (not session_id or row.get("session_id") == session_id)], key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)


def create_ai_message(payload: dict) -> dict:
    conversation_id = payload.get("conversation_id")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    role = str(payload.get("role") or "user")
    if role not in {"user", "assistant", "system"}:
        raise ValueError("Unsupported analyst message role")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValueError("Analyst message content is required")
    item = {"id": f"msg-{len(demo_store['ai_messages']) + 1:05d}", "conversation_id": conversation_id, "role": role, "content": content[:12000], "citations": payload.get("citations") or [], "model": payload.get("model"), "created_at": utc_now()}
    message = _create_item("ai_messages", item)
    conversation = next((row for row in demo_store["ai_conversations"] if row.get("id") == conversation_id), None)
    if conversation:
        conversation["updated_at"] = item["created_at"]
    return message


def list_ai_messages(conversation_id: str) -> list[dict]:
    return sorted([row for row in list_items("ai_messages") if row.get("conversation_id") == conversation_id], key=lambda row: row.get("created_at") or "")


def analytics(event_id: str = "evt-001") -> dict:
    # Read from the configured storage adapter so deployed reports reflect
    # Supabase data instead of the local demo fixture.
    sessions = [item for item in list_items("sessions") if item.get("event_id") == event_id]
    session_ids = {item["id"] for item in sessions}
    feedback = [item for item in list_items("feedback") if item.get("session_id") in session_ids]
    ratings = [item["session_rating"] for item in feedback if item.get("session_rating") is not None]
    transcripts = [item for item in list_items("transcripts") if not session_ids or item.get("session_id") in session_ids]
    questions = [item for item in list_items("questions") if not session_ids or item.get("session_id") in session_ids]
    assets = [item for item in list_items("generated_assets") if not event_id or item.get("event_id") == event_id]
    poll_responses = list_items("poll_responses")
    return {"event_id": event_id, "sessions": len(sessions), "attendees": sum(item.get("attendance", 0) for item in sessions), "transcripts": len(transcripts), "questions": len(questions), "poll_responses": len(poll_responses), "feedback_responses": len(feedback), "generated_assets": len(assets), "average_session_rating": round(sum(ratings) / len(ratings), 2) if ratings else None}


def event_intelligence(event_id: str | None = None) -> dict:
    """Build an event-wide, evidence-linked intelligence snapshot."""
    events = list_items("events")
    event = next((row for row in events if row.get("id") == event_id), None) if event_id else (events[0] if events else None)
    selected_id = event.get("id") if event else event_id
    sessions = [row for row in list_items("sessions") if not selected_id or row.get("event_id") == selected_id]
    session_ids = {row.get("id") for row in sessions}
    transcripts = [row for row in list_items("transcripts") if row.get("session_id") in session_ids]
    insights = [row for row in list_items("insights") if row.get("session_id") in session_ids or row.get("event_id") == selected_id]
    takeaways = [row for row in list_items("takeaways") if row.get("session_id") in session_ids or row.get("event_id") == selected_id]
    questions = [row for row in list_items("questions") if row.get("session_id") in session_ids]
    topics = topic_cloud(selected_id)
    session_intelligence = []
    for session in sessions:
        session_rows = [row for row in transcripts if row.get("session_id") == session.get("id")]
        session_topics = topic_cloud(selected_id, session.get("id"))[:8]
        evidence = [{"id": row.get("id"), "speaker": row.get("speaker"), "timestamp": row.get("created_at"), "snippet": (row.get("text") or "")[:240]} for row in session_rows[:5]]
        session_intelligence.append({"session_id": session.get("id"), "title": session.get("title"), "track": session.get("track"), "status": session.get("status"), "transcript_count": len(session_rows), "topics": session_topics, "evidence": evidence})
    cross_session = []
    for topic in topics:
        related = [item for item in session_intelligence if any(t.get("label") == topic.get("label") for t in item.get("topics", []))]
        if len(related) > 1:
            cross_session.append({"topic": topic.get("label"), "frequency": topic.get("count", 0), "sessions": [{"session_id": item["session_id"], "title": item["title"], "track": item["track"]} for item in related], "evidence": topic.get("evidence", [])[:5]})
    question_themes = [{"id": row.get("id"), "session_id": row.get("session_id"), "status": row.get("status"), "body": row.get("body", ""), "votes": row.get("votes", 0)} for row in questions]
    return {"event": event, "mode": "grounded-local", "sessions": session_intelligence, "themes": topics, "cross_session_themes": cross_session[:20], "takeaways": takeaways[:20], "insights": insights[:20], "questions": question_themes[:50], "source_counts": {"sessions": len(sessions), "transcripts": len(transcripts), "insights": len(insights), "takeaways": len(takeaways), "questions": len(questions)}}


def search_knowledge(query: str, event_id: str | None = None) -> list[dict]:
    """Search normalized event evidence and return traceable, ranked results.

    This is the provider-neutral retrieval layer used before Gemini. It keeps
    the system useful in demo mode while making normalized transcript segments,
    takeaways, questions, summaries, and reports first-class evidence sources.
    """
    normalized_query = " ".join(query.lower().split())
    terms = {part for part in re.findall(r"[a-z0-9][a-z0-9'-]{1,}", normalized_query)}
    if not terms:
        return []
    sessions = {item.get("id"): item for item in list_items("sessions")}
    collections = [
        ("session", list_items("sessions")),
        ("transcript", list_items("transcripts")),
        ("transcript_segment", list_items("transcript_segments")),
        ("insight", list_items("insights")),
        ("takeaway", list_items("takeaways")),
        ("question", list_items("questions")),
        ("summary", list_items("summaries")),
        ("asset", list_items("generated_assets")),
        ("report", list_items("reports")),
    ]
    results: list[dict] = []
    for kind, rows in collections:
        for row in rows:
            if event_id and row.get("event_id") and row.get("event_id") != event_id:
                continue
            searchable = " ".join(str(row.get(key, "")) for key in ("title", "name", "text", "body", "summary", "content", "asset_type")).lower()
            score = sum(1 for term in terms if term in searchable)
            if normalized_query and normalized_query in searchable:
                score += 3
            if kind == "transcript_segment":
                score += 1
            if not score:
                continue
            session = sessions.get(row.get("session_id")) or sessions.get(row.get("id"))
            snippet = row.get("text") or row.get("body") or row.get("summary") or str(row.get("content", ""))
            results.append({"type": kind, "score": score, "id": row.get("id"), "title": row.get("title") or row.get("name") or snippet.split(".", 1)[0], "snippet": str(snippet)[:360], "session_id": row.get("session_id") or row.get("id"), "session_title": session.get("title") if session else None, "speaker": row.get("speaker"), "created_at": row.get("created_at"), "source_type": kind, "source_id": row.get("id")})
    return sorted(results, key=lambda item: (item["score"], item.get("created_at") or ""), reverse=True)[:50]


def topic_cloud(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    """Build a lightweight, evidence-backed topic cloud from captured content."""
    stopwords = {"about", "across", "after", "again", "also", "because", "being", "could", "from", "have", "into", "more", "most", "that", "their", "there", "these", "they", "this", "with", "what", "when", "where", "which", "will", "would", "your", "the", "and", "for", "are", "not", "our", "you", "was", "but", "can", "how", "its", "all", "one", "out", "than", "then", "them", "those", "very", "just", "live", "session", "event", "shared", "signals", "attendees", "new"}
    rows = list_items("transcripts") + list_items("insights")
    counts: dict[str, int] = {}
    evidence: dict[str, list[dict]] = {}
    for row in rows:
        if event_id and row.get("event_id") and row.get("event_id") != event_id:
            continue
        if session_id and row.get("session_id") != session_id:
            continue
        source_text = f"{row.get('text', '')} {row.get('title', '')} {row.get('body', '')}"
        words = {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]{3,}", source_text)} - stopwords
        for word in words:
            counts[word] = counts.get(word, 0) + 1
            evidence.setdefault(word, []).append({"id": row.get("id"), "session_id": row.get("session_id"), "snippet": (row.get("text") or row.get("body") or row.get("title") or "")[:220]})
    maximum = max(counts.values(), default=1)
    return [{"label": label, "count": count, "weight": round(count / maximum, 2), "evidence": evidence[label][:5]} for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:30]]
