import httpx
import re
from .config import settings
from .demo_store import demo_store, utc_now

TABLES = {"events", "sessions", "attendees", "insights", "share_links", "transcripts", "questions", "question_votes", "polls", "poll_options", "poll_responses", "feedback", "generated_assets"}


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
    sessions = [item for item in list_items("sessions") if item.get("event_id") == event_id] if event else []
    return {"link": link, "event": event or (events[0] if events else None), "sessions": sessions}


def dashboard() -> dict:
    events, sessions, attendees, insights, share_links, transcripts = [list_items(name) for name in ("events", "sessions", "attendees", "insights", "share_links", "transcripts")]
    event_id = events[0].get("id", "evt-001") if events else "evt-001"
    return {"event": events[0] if events else None, "sessions": sessions, "attendees": attendees, "insights": insights, "share_links": share_links, "transcripts": transcripts, "analytics": analytics(event_id), "mode": storage_mode()}


def _remote_insert(table: str, item: dict) -> dict | None:
    """Insert a row while letting Supabase generate UUID primary keys."""
    # The dashboard may temporarily be rendering local demo rows while a new
    # Supabase project has no seed data. Never send those readable demo IDs to
    # UUID foreign-key columns; keep the action local until real rows exist.
    foreign_keys = ("event_id", "session_id", "poll_id", "option_id")
    if any(str(item.get(key, "")).startswith(("evt-", "ses-", "poll-", "q-")) for key in foreign_keys if item.get(key)):
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
    session = next((item for item in demo_store["sessions"] if item.get("id") == session_id), None)
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
    index = next((index for index, item in enumerate(demo_store["sessions"]) if item.get("id") == session_id), None)
    if index is None:
        raise KeyError("Session not found")
    removed = demo_store["sessions"].pop(index)
    if storage_mode() == "supabase" and not str(session_id).startswith("ses-"):
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/sessions?id=eq.{session_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "session": removed}


def duplicate_session(session_id: str) -> dict:
    source = next((item for item in demo_store["sessions"] if item.get("id") == session_id), None)
    if not source:
        raise KeyError("Session not found")
    return create_session({"event_id": source.get("event_id"), "title": f"{source.get('title', 'Session')} (copy)", "track": source.get("track"), "room": source.get("room"), "speaker": source.get("speaker"), "starts_at": source.get("starts_at"), "ends_at": source.get("ends_at")})


def create_transcript(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"trn-{len(demo_store['transcripts']) + 1:03d}", "event_id": event_id, "session_id": session_id, "language": payload.get("language", "auto"), "model": payload.get("model", ""), "text": payload.get("text", ""), "speaker": payload.get("speaker", "Live speaker"), "created_at": utc_now()}
    demo_store["transcripts"].insert(0, item)
    if storage_mode() == "supabase":
        return _remote_insert("transcripts", item) or item
    return item


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
            if not item.get("organization_id"): return item
            remote_item = {key: value for key, value in item.items() if key not in {"id", "name"}}
            remote_item["title"] = item.get("name") or item.get("title") or "New event"
            return _remote_insert("events", remote_item) or item
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
    item = {"id": f"evt-{number:03d}", "organization_id": payload.get("organization_id"), "name": payload.get("name") or payload.get("title") or "New event", "slug": payload.get("slug") or f"event-{number:03d}", "venue": payload.get("venue", ""), "starts_at": payload.get("starts_at") or utc_now(), "ends_at": payload.get("ends_at") or utc_now(), "status": payload.get("status", "draft"), "brand_color": payload.get("brand_color", "#7568f3")}
    return _create_item("events", item)


def delete_event(event_id: str) -> dict:
    index = next((index for index, item in enumerate(demo_store["events"]) if item.get("id") == event_id), None)
    if index is None:
        raise KeyError("Event not found")
    removed = demo_store["events"].pop(index)
    demo_store["sessions"][:] = [item for item in demo_store["sessions"] if item.get("event_id") != event_id]
    if storage_mode() == "supabase" and not str(event_id).startswith("evt-"):
        response = httpx.delete(f"{settings.supabase_url}/rest/v1/events?id=eq.{event_id}", headers=_headers(), timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
    return {"deleted": True, "event": removed}


def update_event(event_id: str, payload: dict) -> dict:
    event = next((item for item in demo_store["events"] if item["id"] == event_id), None)
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
    question = next((item for item in demo_store["questions"] if item["id"] == question_id), None)
    if not question:
        raise KeyError("Question not found")
    vote_key = f"{question_id}:{voter_id}"
    if not any(item["id"] == vote_key for item in demo_store["question_votes"]):
        demo_store["question_votes"].append({"id": vote_key, "question_id": question_id, "voter_id": voter_id, "created_at": utc_now()})
        question["votes"] = question.get("votes", 0) + 1
    return question


def create_poll(payload: dict) -> dict:
    number = len(demo_store["polls"]) + 1
    _, session_id = _default_foreign_keys(payload)
    item = {"id": f"poll-{number:03d}", "session_id": session_id, "question": payload["question"].strip(), "poll_type": payload.get("poll_type", "single"), "is_open": False, "created_at": utc_now()}
    saved = _create_item("polls", item)
    for index, label in enumerate(payload.get("options", [])):
        _create_item("poll_options", {"id": f"{saved['id']}-opt-{index + 1}", "poll_id": saved["id"], "label": label, "sort_order": index})
    return saved


def respond_poll(payload: dict) -> dict:
    item = {"id": f"response-{len(demo_store['poll_responses']) + 1:03d}", "poll_id": payload["poll_id"], "option_id": payload.get("option_id"), "attendee_id": payload.get("attendee_id", "anonymous"), "answer_text": payload.get("answer_text"), "rating": payload.get("rating"), "created_at": utc_now()}
    return _create_item("poll_responses", item)


def create_feedback(payload: dict) -> dict:
    item = {"id": f"feedback-{len(demo_store['feedback']) + 1:03d}", "session_id": payload.get("session_id", "ses-001"), "speaker_rating": payload.get("speaker_rating"), "content_rating": payload.get("content_rating"), "session_rating": payload.get("session_rating"), "comment": payload.get("comment", ""), "created_at": utc_now()}
    return _create_item("feedback", item)


def create_generated_asset(payload: dict) -> dict:
    event_id, session_id = _default_foreign_keys(payload)
    item = {"id": f"asset-{len(demo_store['generated_assets']) + 1:03d}", "event_id": event_id, "session_id": session_id, "asset_type": payload.get("asset_type", "attendee_recap"), "title": payload.get("title", "Generated event asset"), "content": payload.get("content", {}), "status": payload.get("status", "draft"), "created_at": utc_now()}
    return _create_item("generated_assets", item)


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


def search_knowledge(query: str, event_id: str | None = None) -> list[dict]:
    """Search the shared event knowledge layer and return traceable results."""
    terms = {part for part in query.lower().split() if len(part) > 1}
    if not terms:
        return []
    sessions = {item.get("id"): item for item in list_items("sessions")}
    collections = [("session", list_items("sessions")), ("transcript", list_items("transcripts")), ("insight", list_items("insights")), ("asset", list_items("generated_assets"))]
    results: list[dict] = []
    for kind, rows in collections:
        for row in rows:
            if event_id and row.get("event_id") and row.get("event_id") != event_id:
                continue
            searchable = " ".join(str(row.get(key, "")) for key in ("title", "name", "text", "body", "summary", "content", "asset_type")).lower()
            score = sum(1 for term in terms if term in searchable)
            if not score:
                continue
            session = sessions.get(row.get("session_id")) or sessions.get(row.get("id"))
            results.append({"type": kind, "score": score, "id": row.get("id"), "title": row.get("title") or row.get("name") or (row.get("text") or row.get("body") or "").split(".", 1)[0], "snippet": (row.get("text") or row.get("body") or row.get("summary") or str(row.get("content", "")))[:360], "session_id": row.get("session_id") or row.get("id"), "session_title": session.get("title") if session else None, "speaker": row.get("speaker"), "created_at": row.get("created_at")})
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
