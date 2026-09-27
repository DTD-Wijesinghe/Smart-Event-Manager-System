# Smart Event Manager API entrypoint.
# Transcript segment persistence is covered by the normalized API routes below.
# Smoke checks use the in-memory demo store and are reset on reload.
# Event intelligence is exposed through the shared /api/intelligence endpoint.
# Attendee portal routes remain usable without organizer credentials.
# Transcript edits preserve a revision trail for organizer review.
from pathlib import Path
import re
import uuid
import logging
import hashlib
import hmac
import secrets
import time
import base64
import json
from typing import Any
import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import FRONTEND_DIR, settings
from .demo_store import demo_store
from .repository import analytics, assign_session_speaker, attendee_matches, create_ai_conversation, create_ai_message, create_event, create_feedback, create_file, create_generated_asset, create_insight, create_invitation, create_poll, create_processing_job, create_question, create_report, create_session, create_speaker, create_takeaway, create_transcript, create_translation as persist_translation, dashboard, delete_event, delete_file, delete_session, delete_speaker, duplicate_session, ensure_share_link, event_intelligence, get_brand_kit, get_share_link, list_ai_conversations, list_ai_messages, list_asset_versions, list_items, list_reports, list_session_speakers, list_summaries, list_transcript_segment_revisions, list_transcript_segments, moderate_question, respond_poll, save_summary, search_knowledge, set_attendee_checkin, set_session_status, storage_mode, team_workspace, topic_cloud, unassign_session_speaker, update_event, update_generated_asset, update_poll, update_processing_job, update_session, update_speaker, update_team_member, update_transcript_segment, upsert_brand_kit, vote_question
from .vertex_ai import analyst_answer, generate_content as generate_content_ai, rewrite_content as rewrite_content_ai, summarize, transcribe, translate

logger = logging.getLogger("smart_event_manager")
_RATE_STATE: dict[str, list[float]] = {}


def _rate_limit_key(request, relative: str) -> tuple[str, int] | None:
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if relative.startswith("auth/"):
        return f"auth:{request.client.host if request.client else 'unknown'}", 10
    if relative.startswith("files/upload"):
        return f"upload:{request.client.host if request.client else 'unknown'}", 10
    return f"write:{request.client.host if request.client else 'unknown'}", 60


def _rate_limited(key: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    recent = [stamp for stamp in _RATE_STATE.get(key, []) if now - stamp < window]
    if len(recent) >= limit:
        _RATE_STATE[key] = recent
        return True
    recent.append(now)
    _RATE_STATE[key] = recent
    return False


def _demo_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 310_000).hex()
    return f"pbkdf2$310000${salt}${digest}"


def _demo_password_matches(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, expected = stored.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _demo_session(email: str) -> dict:
    user = next((item for item in demo_store.setdefault("auth_users", []) if item.get("email") == email), {})
    role = user.get("role", "organization_admin")
    access = _demo_signed_token({"type": "access", "email": email, "role": role, "exp": int(time.time()) + 3600})
    refresh = _demo_signed_token({"type": "refresh", "email": email, "exp": int(time.time()) + 2592000})
    demo_store.setdefault("auth_sessions", {})[access] = {"email": email, "role": role, "refresh_token": refresh}
    demo_store.setdefault("auth_refresh_tokens", {})[refresh] = email
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "user": {"email": email, "role": role}}


def _demo_signed_token(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    signature = hmac.new(settings.demo_session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"demo.{body}.{signature}"


def _demo_token_payload(token: str, token_type: str) -> dict | None:
    try:
        prefix, body, signature = token.split(".", 2)
        if prefix != "demo" or not hmac.compare_digest(signature, hmac.new(settings.demo_session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("type") != token_type or int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


class SummaryRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


class SessionSummaryRequest(BaseModel):
    targetLanguage: str = "English"


class TranslateRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


class TranslationPersistRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


class TranscriptSegmentUpdateRequest(BaseModel):
    text: str
    editor: str = "organizer"


class CaptureRequest(BaseModel):
    text: str
    session_id: str = "ses-001"
    language: str = "auto"
    speaker: str = "Live speaker"


class QuestionRequest(BaseModel):
    body: str
    session_id: str = "ses-001"
    anonymous: bool = False


class QuestionModerationRequest(BaseModel):
    status: str | None = None
    pinned: bool | None = None


class VoteRequest(BaseModel):
    voter_id: str = "anonymous"


class CheckInRequest(BaseModel):
    checked_in: bool


class PollRequest(BaseModel):
    question: str
    session_id: str = "ses-001"
    poll_type: str = "single"
    options: list[str] = []


class PollUpdateRequest(BaseModel):
    is_open: bool | None = None


class PollResponseRequest(BaseModel):
    poll_id: str
    option_id: str | None = None
    option_ids: list[str] = []
    attendee_id: str = "anonymous"
    answer_text: str | None = None
    rating: int | None = None


class FeedbackRequest(BaseModel):
    session_id: str = "ses-001"
    speaker_rating: int | None = None
    content_rating: int | None = None
    session_rating: int | None = None
    comment: str = ""


class ContentGenerateRequest(BaseModel):
    text: str
    targetLanguage: str = "English"
    asset_type: str = "attendee_recap"
    title: str = "Generated event asset"
    event_id: str | None = None
    session_id: str | None = None


class ReportGenerateRequest(BaseModel):
    event_id: str | None = None
    session_ids: list[str] = []
    report_type: str = "executive_brief"
    title: str = "Event intelligence report"
    format: str = "markdown"
    targetLanguage: str = "English"
    sections: list[str] = []


class AssetUpdateRequest(BaseModel):
    title: str | None = None
    content: dict | None = None
    status: str | None = None


class AssetRewriteRequest(BaseModel):
    instruction: str
    targetLanguage: str = "English"


class SpeakerRequest(BaseModel):
    organization_id: str | None = None
    name: str
    title: str = ""
    company: str = ""
    biography: str = ""
    photo_url: str = ""
    profile_url: str = ""


class SessionSpeakerRequest(BaseModel):
    speaker_id: str
    sort_order: int = 0


class TakeawayGenerateRequest(BaseModel):
    text: str = ""
    targetLanguage: str = "English"
    event_id: str | None = None
    session_id: str | None = None
    title: str = "Event takeaway"


class AuthRequest(BaseModel):
    email: str
    password: str


class RecoveryRequest(BaseModel):
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ResetPasswordRequest(BaseModel):
    password: str


class InvitationRequest(BaseModel):
    email: str
    role: str = "event_organizer"
    organization_id: str = "org-demo"


class TeamMemberUpdateRequest(BaseModel):
    role: str | None = None
    status: str | None = None


class BrandKitRequest(BaseModel):
    organization_id: str = "org-demo"
    name: str = "Default brand"
    logo_url: str = ""
    primary_color: str = "#7568f3"
    secondary_color: str = "#1b1c2d"
    accent_color: str = "#e4ff63"
    font_family: str = "Inter"
    tone: str = "clear, generous, modern"
    website: str = ""


class AnalystRequest(BaseModel):
    question: str
    event_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None


app = FastAPI(title="Smart Event Manager API", version="1.0.0")  # versioned content build verified
# Runtime build marker: upload/job pipeline and guarded auth enabled.
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=settings.allowed_origins != ["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def protect_api(request, call_next):
    """Validate Supabase sessions for organizer APIs when database mode is enabled.

    Demo mode remains usable locally, while deployed Supabase mode rejects
    unauthenticated organizer requests before they reach the repository.
    Public share links and auth bootstrap routes stay accessible.
    """
    path = request.url.path
    public = {f"{settings.api_prefix}/health"} | {f"{settings.api_prefix}/auth/{name}" for name in ("register", "login", "forgot-password", "refresh", "reset-password", "logout")}
    relative = path.removeprefix(f"{settings.api_prefix}/")
    method = request.method.upper()
    rate_key = _rate_limit_key(request, relative) if path.startswith(f"{settings.api_prefix}/") else None
    if rate_key and _rate_limited(*rate_key):
        return JSONResponse(status_code=429, content={"detail": "Too many requests. Please retry shortly."}, headers={"Retry-After": "60"})
    attendee_public = (
        relative.startswith("public/share")
        or (relative == "questions" and method in {"GET", "POST"})
        or (relative.startswith("questions/") and relative.endswith("/votes") and method == "POST")
        or (relative == "polls" and method == "GET")
        or (relative == "poll-responses" and method == "POST")
        or (relative == "feedback" and method == "POST")
        or (relative == "transcripts" and method == "GET")
        or (relative == "transcript-segments" and method == "GET")
        or (relative.startswith("transcript-segments/") and method in {"GET", "POST"} and (relative.endswith("/translate") or relative.endswith("/translations")))
        or (relative == "topics" and method == "GET")
    )
    if not path.startswith(f"{settings.api_prefix}/") or path in public or attendee_public:
        return await call_next(request)
    authorization = request.headers.get("authorization")
    if not authorization:
        return JSONResponse(status_code=401, content={"detail": "Organizer login is required"})
    token = authorization.removeprefix("Bearer ").strip()
    role = ""
    try:
        if not settings.supabase_url:
            session = demo_store.setdefault("auth_sessions", {}).get(token)
            if not session:
                signed = _demo_token_payload(token, "access")
                if signed:
                    session = {"email": signed["email"], "role": signed.get("role", "organization_admin")}
            if not session:
                return JSONResponse(status_code=401, content={"detail": "Organizer session is invalid or expired"})
            role = session.get("role", "organization_admin")
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{settings.supabase_url}/auth/v1/user", headers={"apikey": settings.supabase_anon_key or settings.supabase_key, "Authorization": authorization})
            if not response.is_success:
                return JSONResponse(status_code=401, content={"detail": "Organizer session is invalid or expired"})
            user = response.json()
            role = (user.get("user_metadata") or {}).get("role", "")
            if not role and user.get("id"):
                role = _supabase_role(user["id"])
            role = role or "organization_admin"
    except httpx.HTTPError:
        return JSONResponse(status_code=503, content={"detail": "Authentication service is unavailable"})
    write_prefixes = ("events", "sessions", "team", "brand-kit", "files", "capture", "transcription", "ws")
    content_prefixes = ("content", "reports", "takeaways", "analyst", "ai", "summaries", "transcript-segments")
    if method not in {"GET", "HEAD", "OPTIONS"}:
        allowed = {"super_admin", "organization_admin", "event_organizer"}
        if relative.startswith(content_prefixes):
            allowed |= {"content_editor"}
        if not any(relative == prefix or relative.startswith(prefix + "/") for prefix in write_prefixes + content_prefixes):
            allowed = {"super_admin", "organization_admin", "event_organizer", "content_editor"}
        if role not in allowed:
            return JSONResponse(status_code=403, content={"detail": f"Role '{role}' cannot perform this action"})
    return await call_next(request)


def _supabase_role(user_id: str) -> str:
    try:
        response = httpx.get(f"{settings.supabase_url}/rest/v1/organization_members?user_id=eq.{user_id}&status=eq.active&select=role&limit=1", headers={"apikey": settings.supabase_key, "Authorization": f"Bearer {settings.supabase_key}"}, timeout=10)
        if response.is_success and response.json():
            return response.json()[0].get("role", "")
    except httpx.HTTPError:
        pass
    return ""


async def _websocket_authenticated(websocket: WebSocket) -> bool:
    """Validate the bearer token used by the live capture bridge."""
    authorization = websocket.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() or websocket.query_params.get("token", "").strip()
    if not token:
        return False
    if not settings.supabase_url:
        return bool(demo_store.setdefault("auth_sessions", {}).get(token))
    key = settings.supabase_anon_key or settings.supabase_key
    if not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{settings.supabase_url}/auth/v1/user", headers={"apikey": key, "Authorization": f"Bearer {token}"})
        return response.is_success
    except httpx.HTTPError:
        return False


@app.get(f"{settings.api_prefix}/health")
def health() -> dict[str, Any]:
    vertex_ready = bool(settings.project and ((settings.credentials_path and Path(settings.credentials_path).exists()) or settings.credentials_json))
    return {"ok": True, "mode": storage_mode(), "vertexConfigured": vertex_ready, "geminiConfigured": bool(settings.gemini_api_key), "aiConfigured": vertex_ready or bool(settings.gemini_api_key), "project": settings.project or None}


def _auth_headers() -> dict[str, str]:
    return {"apikey": settings.supabase_anon_key, "Content-Type": "application/json"}


@app.post(f"{settings.api_prefix}/auth/register", status_code=201)
def register(request: AuthRequest) -> dict:
    if len(request.password) < 8: raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not settings.supabase_url or not settings.supabase_anon_key:
        email = request.email.strip().lower()
        users = demo_store.setdefault("auth_users", [])
        if any(user.get("email") == email for user in users):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        users.append({"id": f"user-demo-{len(users) + 1:04d}", "email": email, "password_hash": _demo_password(request.password), "role": "organization_admin"})
        return {"mode": "demo", "session": _demo_session(email)}
    response = httpx.post(f"{settings.supabase_url}/auth/v1/signup", headers=_auth_headers(), json={"email": request.email, "password": request.password}, timeout=20)
    if not response.is_success:
        detail = response.json().get("msg") or response.json().get("error_description") or "Registration failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return {"mode": "supabase", "session": response.json()}


@app.post(f"{settings.api_prefix}/auth/login")
def login(request: AuthRequest) -> dict:
    if not settings.supabase_url or not settings.supabase_anon_key:
        email = request.email.strip().lower()
        user = next((item for item in demo_store.setdefault("auth_users", []) if item.get("email") == email), None)
        if not user or not _demo_password_matches(request.password, user.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {"mode": "demo", "session": _demo_session(email)}
    response = httpx.post(f"{settings.supabase_url}/auth/v1/token?grant_type=password", headers=_auth_headers(), json={"email": request.email, "password": request.password}, timeout=20)
    if not response.is_success:
        detail = response.json().get("error_description") or response.json().get("msg") or "Login failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return {"mode": "supabase", "session": response.json()}


@app.post(f"{settings.api_prefix}/auth/forgot-password")
def forgot_password(request: RecoveryRequest) -> dict:
    if settings.supabase_url and settings.supabase_anon_key:
        response = httpx.post(f"{settings.supabase_url}/auth/v1/recover", headers=_auth_headers(), json={"email": request.email}, timeout=20)
        if not response.is_success:
            detail = response.json().get("msg") or "Password recovery request failed"
            raise HTTPException(status_code=response.status_code, detail=detail)
        return {"ok": True, "message": "If the email exists, a reset link is on its way"}
    email = request.email.strip().lower()
    user = next((item for item in demo_store.setdefault("auth_users", []) if item.get("email") == email), None)
    result = {"ok": True, "message": "If the email exists, a reset link is on its way"}
    if user:
        token = f"demo-reset-{secrets.token_urlsafe(32)}"
        demo_store.setdefault("auth_reset_tokens", {})[token] = email
        result["reset_token"] = token
    return result


@app.post(f"{settings.api_prefix}/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    if not settings.supabase_url and authorization:
        token = authorization.removeprefix("Bearer ").strip()
        demo_store.setdefault("auth_sessions", {}).pop(token, None)
        return {"ok": True}
    if settings.supabase_url and settings.supabase_anon_key and authorization:
        response = httpx.post(f"{settings.supabase_url}/auth/v1/logout", headers={"apikey": settings.supabase_anon_key, "Authorization": authorization}, timeout=20)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail="Logout failed")
    return {"ok": True}


@app.post(f"{settings.api_prefix}/auth/refresh")
def refresh_session(request: RefreshRequest) -> dict:
    if not request.refresh_token.strip():
        raise HTTPException(status_code=400, detail="Refresh token is required")
    if not settings.supabase_url or not settings.supabase_anon_key:
        email = demo_store.setdefault("auth_refresh_tokens", {}).get(request.refresh_token)
        if not email:
            signed = _demo_token_payload(request.refresh_token, "refresh")
            email = signed.get("email") if signed else None
        if not email:
            raise HTTPException(status_code=401, detail="Refresh session is invalid or expired")
        return {"mode": "demo", "session": _demo_session(email)}
    response = httpx.post(f"{settings.supabase_url}/auth/v1/token?grant_type=refresh_token", headers=_auth_headers(), json={"refresh_token": request.refresh_token}, timeout=20)
    if not response.is_success:
        raise HTTPException(status_code=response.status_code, detail="Session refresh failed")
    return {"mode": "supabase", "session": response.json()}


@app.post(f"{settings.api_prefix}/auth/reset-password")
def reset_password(request: ResetPasswordRequest, authorization: str | None = Header(default=None)) -> dict:
    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not settings.supabase_url or not settings.supabase_anon_key:
        token = (authorization or "").removeprefix("Bearer ").strip()
        email = demo_store.setdefault("auth_reset_tokens", {}).pop(token, None)
        if not email:
            raise HTTPException(status_code=401, detail="Recovery link is invalid or expired")
        user = next((item for item in demo_store.setdefault("auth_users", []) if item.get("email") == email), None)
        if not user:
            raise HTTPException(status_code=401, detail="Recovery link is invalid or expired")
        user["password_hash"] = _demo_password(request.password)
        return {"mode": "demo", "ok": True, "message": "Password updated"}
    if not authorization:
        raise HTTPException(status_code=401, detail="Recovery authorization is required")
    response = httpx.put(
        f"{settings.supabase_url}/auth/v1/user",
        headers={"apikey": settings.supabase_anon_key, "Authorization": authorization, "Content-Type": "application/json"},
        json={"password": request.password},
        timeout=20,
    )
    if not response.is_success:
        detail = response.json().get("msg") or response.json().get("error_description") or "Password reset failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return {"mode": "supabase", "ok": True, "message": "Password updated"}


@app.get(f"{settings.api_prefix}/dashboard")
def get_dashboard(event_id: str | None = None) -> dict: return dashboard(event_id)


@app.get(f"{settings.api_prefix}/sessions")
def get_sessions() -> list[dict]: return list_items("sessions")


@app.post(f"{settings.api_prefix}/sessions", status_code=201)
def add_session(payload: dict) -> dict: return create_session(payload)


@app.patch(f"{settings.api_prefix}/sessions/{{session_id}}")
def edit_session(session_id: str, payload: dict) -> dict:
    try: return update_session(session_id, payload)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/duplicate", status_code=201)
def copy_session(session_id: str) -> dict:
    try: return duplicate_session(session_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(f"{settings.api_prefix}/sessions/{{session_id}}")
def remove_session(session_id: str) -> dict:
    try: return delete_session(session_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/start")
def start_session(session_id: str) -> dict:
    try: return set_session_status(session_id, "live")
    except (KeyError, ValueError) as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/stop")
def stop_session(session_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        stopped = set_session_status(session_id, "completed")
        background_tasks.add_task(_generate_completed_session_summary, session_id)
        return {**stopped, "summary_status": "queued"}
    except (KeyError, ValueError) as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


def _generate_completed_session_summary(session_id: str) -> None:
    """Generate a session summary after Stop without blocking the control action."""
    try:
        generate_session_summary(session_id, SessionSummaryRequest(targetLanguage="English"))
    except Exception as exc:
        logger.warning("completed-session summary failed for %s: %s", session_id, exc)


@app.get(f"{settings.api_prefix}/events")
def get_events() -> list[dict]: return list_items("events")


@app.get(f"{settings.api_prefix}/speakers")
def get_speakers(organization_id: str | None = None) -> list[dict]:
    return [item for item in list_items("speakers") if not organization_id or item.get("organization_id") == organization_id]


@app.post(f"{settings.api_prefix}/speakers", status_code=201)
def add_speaker(request: SpeakerRequest) -> dict:
    try:
        return create_speaker(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch(f"{settings.api_prefix}/speakers/{{speaker_id}}")
def edit_speaker(speaker_id: str, request: SpeakerRequest) -> dict:
    try:
        return update_speaker(speaker_id, request.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(f"{settings.api_prefix}/speakers/{{speaker_id}}")
def remove_speaker(speaker_id: str) -> dict:
    try:
        return delete_speaker(speaker_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/sessions/{{session_id}}/speakers")
def get_session_speakers(session_id: str) -> list[dict]:
    return list_session_speakers(session_id)


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/speakers", status_code=201)
def add_session_speaker(session_id: str, request: SessionSpeakerRequest) -> dict:
    try:
        return assign_session_speaker(session_id, request.speaker_id, request.sort_order)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(f"{settings.api_prefix}/sessions/{{session_id}}/speakers/{{speaker_id}}")
def remove_session_speaker(session_id: str, speaker_id: str) -> dict:
    try:
        return unassign_session_speaker(session_id, speaker_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/team")
def get_team(organization_id: str | None = None) -> dict: return team_workspace(organization_id)


@app.post(f"{settings.api_prefix}/team/invitations", status_code=201)
def invite_team_member(request: InvitationRequest) -> dict:
    try: return create_invitation(request.model_dump())
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch(f"{settings.api_prefix}/team/members/{{member_id}}")
def edit_team_member(member_id: str, request: TeamMemberUpdateRequest) -> dict:
    try: return update_team_member(member_id, request.role, request.status)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/brand-kit")
def get_brand(organization_id: str | None = None) -> dict: return get_brand_kit(organization_id)


@app.put(f"{settings.api_prefix}/brand-kit")
def save_brand(request: BrandKitRequest) -> dict:
    for value in (request.primary_color, request.secondary_color, request.accent_color):
        if value and (not value.startswith("#") or len(value) not in {4, 7}):
            raise HTTPException(status_code=400, detail="Brand colors must be valid hex values")
    return upsert_brand_kit(request.model_dump())


@app.post(f"{settings.api_prefix}/events", status_code=201)
def add_event(payload: dict) -> dict: return create_event(payload)


@app.patch(f"{settings.api_prefix}/events/{{event_id}}")
def edit_event(event_id: str, payload: dict) -> dict:
    try: return update_event(event_id, payload)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(f"{settings.api_prefix}/events/{{event_id}}")
def remove_event(event_id: str) -> dict:
    try: return delete_event(event_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/attendees")
def get_attendees() -> list[dict]: return list_items("attendees")


@app.get(f"{settings.api_prefix}/attendees/matches")
def get_attendee_matches(attendee_id: str) -> list[dict]: return attendee_matches(attendee_id)


@app.post(f"{settings.api_prefix}/attendees/{{attendee_id}}/check-in")
def check_in_attendee(attendee_id: str, request: CheckInRequest) -> dict:
    try: return set_attendee_checkin(attendee_id, request.checked_in)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/insights")
def get_insights() -> list[dict]: return list_items("insights")


@app.get(f"{settings.api_prefix}/share-links")
def get_share_links(event_id: str | None = None) -> list[dict]:
    if event_id:
        try: return [ensure_share_link(event_id)]
        except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list_items("share_links")


@app.get(f"{settings.api_prefix}/public/share/{{token}}")
def get_public_share(token: str) -> dict:
    result = get_share_link(token)
    if not result:
        raise HTTPException(status_code=404, detail="Share link not found")
    return result


@app.get(f"{settings.api_prefix}/transcripts")
def get_transcripts(session_id: str | None = None) -> list[dict]:
    rows = list_items("transcripts")
    return [row for row in rows if not session_id or row.get("session_id") == session_id]


@app.get(f"{settings.api_prefix}/summaries")
def get_summaries(session_id: str | None = None, event_id: str | None = None) -> list[dict]:
    return list_summaries(session_id=session_id, event_id=event_id)


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/summary", status_code=201)
def generate_session_summary(session_id: str, request: SessionSummaryRequest) -> dict:
    session = next((row for row in list_items("sessions") if row.get("id") == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    rows = [row for row in list_items("transcripts") if row.get("session_id") == session_id]
    source = "\n".join(row.get("text", "") for row in reversed(rows)).strip()
    if not source:
        raise HTTPException(status_code=400, detail="Capture transcript evidence before generating a summary")
    try:
        result = summarize(source, request.targetLanguage)
        mode = "ai"
    except Exception:
        result = {"output": f"Overview\n{source[:360]}\n\nMain discussion points\n• {source[:240]}\n\nAction items\n• Review this evidence with the event team.", "model": "local-grounded"}
        mode = "fallback"
    content = {
        "output": result.get("output", ""),
        "sections": ["overview", "main_discussion_points", "important_insights", "decisions", "recommendations", "questions_raised", "action_items", "notable_quotes", "topics"],
        "evidence": [{"id": row.get("id"), "speaker": row.get("speaker"), "timestamp": row.get("created_at"), "snippet": (row.get("text") or "")[:240]} for row in rows[:12]],
    }
    saved = save_summary({"event_id": session.get("event_id"), "session_id": session_id, "language": request.targetLanguage, "content": content, "model": result.get("model", "local-grounded")})
    return {"mode": mode, "summary": saved}


@app.get(f"{settings.api_prefix}/transcript-segments")
def get_transcript_segments(session_id: str | None = None) -> list[dict]:
    return list_transcript_segments(session_id)


@app.patch(f"{settings.api_prefix}/transcript-segments/{{segment_id}}")
def edit_transcript_segment(segment_id: str, request: TranscriptSegmentUpdateRequest) -> dict:
    try:
        return update_transcript_segment(segment_id, request.text, request.editor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/transcript-segments/{{segment_id}}/revisions")
def get_transcript_segment_revisions(segment_id: str) -> list[dict]:
    return list_transcript_segment_revisions(segment_id)


@app.post(f"{settings.api_prefix}/transcript-segments/{{segment_id}}/translate", status_code=201)
def translate_transcript_segment(segment_id: str, request: TranslationPersistRequest) -> dict:
    segment = next((item for item in list_transcript_segments() if item.get("id") == segment_id), None)
    if not segment:
        raise HTTPException(status_code=404, detail="Transcript segment not found")
    try:
        result = translate(request.text, request.targetLanguage)
    except Exception:
        result = {"output": f"[{request.targetLanguage}] {request.text}", "mode": "fallback", "targetLanguage": request.targetLanguage}
    saved = persist_translation({"transcript_segment_id": segment_id, "language": request.targetLanguage, "text": result.get("output", "")})
    return {**result, "translation": saved}


@app.get(f"{settings.api_prefix}/transcript-segments/{{segment_id}}/translations")
def get_segment_translations(segment_id: str) -> list[dict]:
    return [item for item in list_items("translations") if item.get("transcript_segment_id") == segment_id]


@app.get(f"{settings.api_prefix}/transcripts/export")
def export_transcripts(format: str = "txt") -> PlainTextResponse:
    rows = list(reversed(list_items("transcripts")))
    normalized = format.lower()
    if normalized not in {"txt", "srt", "vtt"}:
        raise HTTPException(status_code=400, detail="Export format must be txt, srt, or vtt")
    if normalized == "txt":
        body = "\n".join(f"[{row.get('created_at', 'LIVE')}] {row.get('speaker') or 'Live speaker'}: {row.get('text', '')}" for row in rows)
        media_type = "text/plain"
    else:
        def stamp(seconds: int, separator: str) -> str:
            return f"00:00:{seconds:02d}{separator}000"
        blocks = []
        for index, row in enumerate(rows):
            start, end = stamp(index, "," if normalized == "srt" else "."), stamp(index + 1, "," if normalized == "srt" else ".")
            blocks.append(f"{index + 1}\n{start} --> {end}\n{row.get('speaker') or 'Live speaker'}: {row.get('text', '')}")
        body = ("WEBVTT\n\n" if normalized == "vtt" else "") + "\n\n".join(blocks)
        media_type = "text/vtt" if normalized == "vtt" else "application/x-subrip"
    return PlainTextResponse(body, media_type=media_type, headers={"Content-Disposition": f"attachment; filename=smart-event-transcript.{normalized}"})


ALLOWED_UPLOAD_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".webm", ".txt", ".srt", ".vtt"}


def _safe_upload_name(name: str) -> str:
    base = Path(name or "upload").name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", base)[:160] or "upload"


async def _process_upload(file_record: dict, job: dict, raw: bytes, language: str) -> None:
    try:
        update_processing_job(job["id"], status="running", progress=10, attempts=int(job.get("attempts") or 0) + 1)
        suffix = Path(file_record["original_name"]).suffix.lower()
        if suffix in {".txt", ".srt", ".vtt"}:
            text = raw.decode("utf-8", errors="replace")
            text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"\d{2}:\d{2}(?::\d{2})?[,.]\d{3}\s*-->.*", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            model = "uploaded-transcript"
        else:
            result = transcribe(raw, file_record["mime_type"], [] if language == "auto" else [language])
            text, model = result.get("transcript", ""), result.get("model", "uploaded-audio")
        if not text.strip():
            raise ValueError("The uploaded file did not contain readable transcript text")
        capture = {"text": text, "model": model, "language": language, "session_id": file_record["session_id"], "speaker": "Uploaded recording"}
        create_transcript(capture)
        create_insight(capture)
        update_processing_job(job["id"], status="completed", progress=100, error_message=None)
    except Exception as exc:
        update_processing_job(job["id"], status="failed", progress=100, error_message=str(exc))


@app.post(f"{settings.api_prefix}/files/upload", status_code=202)
async def upload_media(background_tasks: BackgroundTasks, file: UploadFile = File(...), session_id: str = Form("ses-001"), event_id: str | None = Form(None), language: str = Form("auto")) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported upload type: {suffix or 'missing extension'}")
    raw = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Upload exceeds the {settings.max_upload_mb} MB limit")
    event_id = event_id or next((item.get("event_id") for item in list_items("sessions") if item.get("id") == session_id), None)
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}-{_safe_upload_name(file.filename or 'upload') }"
    destination = upload_dir / stored_name
    destination.write_bytes(raw)
    file_record = create_file({"event_id": event_id, "session_id": session_id, "original_name": file.filename or "upload", "storage_path": str(destination), "mime_type": file.content_type or "application/octet-stream", "size_bytes": len(raw), "status": "processing"})
    job = create_processing_job({"event_id": event_id, "session_id": session_id, "job_type": "transcription", "status": "queued", "progress": 0})
    background_tasks.add_task(_process_upload, file_record, job, raw, language)
    return {"file": file_record, "job": job, "status": "queued"}


@app.get(f"{settings.api_prefix}/files")
def get_files(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    return [item for item in list_items("files") if (not event_id or item.get("event_id") == event_id) and (not session_id or item.get("session_id") == session_id)]


@app.delete(f"{settings.api_prefix}/files/{{file_id}}")
def remove_file(file_id: str) -> dict:
    try:
        result = delete_file(file_id)
        path = result["file"].get("storage_path")
        if path:
            stored = Path(path).resolve()
            upload_root = settings.data_dir.resolve() / "uploads"
            if stored.parent == upload_root and stored.exists():
                stored.unlink()
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/processing-jobs")
def get_processing_jobs(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    return [item for item in list_items("processing_jobs") if (not event_id or item.get("event_id") == event_id) and (not session_id or item.get("session_id") == session_id)]


@app.get(f"{settings.api_prefix}/questions")
def get_questions(session_id: str | None = None) -> list[dict]:
    questions = list_items("questions")
    return [item for item in questions if not session_id or item.get("session_id") == session_id]


@app.post(f"{settings.api_prefix}/questions", status_code=201)
def add_question(request: QuestionRequest) -> dict:
    if len(request.body.strip()) < 3: raise HTTPException(status_code=400, detail="Question is too short")
    return create_question(request.model_dump())


@app.post(f"{settings.api_prefix}/questions/{{question_id}}/votes")
def add_question_vote(question_id: str, request: VoteRequest) -> dict:
    try: return vote_question(question_id, request.voter_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch(f"{settings.api_prefix}/questions/{{question_id}}")
def moderate_question_route(question_id: str, request: QuestionModerationRequest) -> dict:
    try: return moderate_question(question_id, request.status, request.pinned)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/polls")
def get_polls(session_id: str | None = None) -> list[dict]:
    polls = list_items("polls")
    options = list_items("poll_options")
    responses = list_items("poll_responses")
    result = []
    for item in polls:
        if session_id and item.get("session_id") != session_id:
            continue
        poll_options = sorted([option for option in options if option.get("poll_id") == item.get("id")], key=lambda option: option.get("sort_order", 0))
        poll_responses = [response for response in responses if response.get("poll_id") == item.get("id")]
        counts = {option.get("id"): 0 for option in poll_options}
        for response in poll_responses:
            if response.get("option_id") in counts:
                counts[response["option_id"]] += 1
        result.append({**item, "options": [{**option, "responses": counts.get(option.get("id"), 0)} for option in poll_options], "results": {"total_responses": len(poll_responses), "text_responses": [response.get("answer_text") for response in poll_responses if response.get("answer_text")]}})
    return result


@app.post(f"{settings.api_prefix}/polls", status_code=201)
def add_poll(request: PollRequest) -> dict:
    if len(request.question.strip()) < 3: raise HTTPException(status_code=400, detail="Poll question is too short")
    if request.poll_type in {"single", "multiple", "yes_no"} and len(request.options) < 2: raise HTTPException(status_code=400, detail="At least two poll options are required")
    created = create_poll(request.model_dump())
    options = sorted([option for option in list_items("poll_options") if option.get("poll_id") == created.get("id")], key=lambda option: option.get("sort_order", 0))
    return {**created, "options": options, "results": {"total_responses": 0, "text_responses": []}}


@app.patch(f"{settings.api_prefix}/polls/{{poll_id}}")
def update_poll_route(poll_id: str, request: PollUpdateRequest) -> dict:
    try: return update_poll(poll_id, request.is_open)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/poll-responses", status_code=201)
def add_poll_response(request: PollResponseRequest) -> dict:
    poll = next((item for item in list_items("polls") if item.get("id") == request.poll_id), None)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    if not poll.get("is_open"):
        raise HTTPException(status_code=409, detail="This poll is closed")
    if request.attendee_id != "anonymous":
        already_voted = any(item.get("poll_id") == request.poll_id and item.get("attendee_id") == request.attendee_id for item in list_items("poll_responses"))
        if already_voted:
            raise HTTPException(status_code=409, detail="You have already responded to this poll")
    selected = request.option_ids or ([request.option_id] if request.option_id else [])
    valid_options = {item.get("id") for item in list_items("poll_options") if item.get("poll_id") == request.poll_id}
    if any(option_id not in valid_options for option_id in selected):
        raise HTTPException(status_code=400, detail="Selected poll option is invalid")
    if poll.get("poll_type") != "multiple" and len(selected) > 1:
        raise HTTPException(status_code=400, detail="This poll accepts one option")
    if len(selected) > 1:
        return {"responses": [respond_poll({**request.model_dump(), "option_id": option_id}) for option_id in selected]}
    return respond_poll({**request.model_dump(), "option_id": selected[0] if selected else None})


@app.post(f"{settings.api_prefix}/feedback", status_code=201)
def add_feedback(request: FeedbackRequest) -> dict:
    for rating in (request.speaker_rating, request.content_rating, request.session_rating):
        if rating is not None and not 1 <= rating <= 5: raise HTTPException(status_code=400, detail="Ratings must be between 1 and 5")
    return create_feedback(request.model_dump())


@app.get(f"{settings.api_prefix}/analytics")
def get_analytics(event_id: str = "evt-001") -> dict: return analytics(event_id)


@app.get(f"{settings.api_prefix}/intelligence")
def get_event_intelligence(event_id: str | None = None) -> dict:
    return event_intelligence(event_id)


@app.get(f"{settings.api_prefix}/search")
def search(query: str, event_id: str | None = None) -> list[dict]:
    if len(query.strip()) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least two characters")
    return search_knowledge(query, event_id)


@app.get(f"{settings.api_prefix}/topics")
def get_topics(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    return topic_cloud(event_id, session_id)


@app.post(f"{settings.api_prefix}/analyst/ask")
def ask_analyst(request: AnalystRequest) -> dict:
    if len(request.question.strip()) < 3:
        raise HTTPException(status_code=400, detail="Ask a longer event question")
    conversation_id = request.conversation_id
    if conversation_id and not any(row.get("id") == conversation_id for row in list_items("ai_conversations")):
        raise HTTPException(status_code=404, detail="Analyst conversation not found")
    if not conversation_id:
        conversation = create_ai_conversation({"event_id": request.event_id, "session_id": request.session_id, "title": request.question.strip()[:120]})
        conversation_id = conversation["id"]
    create_ai_message({"conversation_id": conversation_id, "role": "user", "content": request.question})
    sources = search_knowledge(request.question, request.event_id)
    if request.session_id:
        sources = [source for source in sources if source.get("session_id") == request.session_id]
    if not sources:
        answer = "I could not find supporting event content for that question yet. Capture or upload a session transcript, then ask again."
        result = {"mode": "grounded", "answer": answer, "citations": []}
        create_ai_message({"conversation_id": conversation_id, "role": "assistant", "content": answer, "citations": []})
        return {"conversation_id": conversation_id, **result}
    try:
        result = {"mode": "ai", **analyst_answer(request.question, sources)}
    except Exception:
        excerpts = " ".join(source["snippet"] for source in sources[:3])
        result = {"mode": "fallback", "answer": f"Relevant event evidence: {excerpts}", "citations": sources[:3]}
    create_ai_message({"conversation_id": conversation_id, "role": "assistant", "content": result["answer"], "citations": result.get("citations", []), "model": result.get("model")})
    return {"conversation_id": conversation_id, **result}


@app.get(f"{settings.api_prefix}/analyst/conversations")
def get_analyst_conversations(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    return list_ai_conversations(event_id, session_id)


@app.get(f"{settings.api_prefix}/analyst/conversations/{{conversation_id}}/messages")
def get_analyst_messages(conversation_id: str) -> list[dict]:
    if not any(row.get("id") == conversation_id for row in list_items("ai_conversations")):
        raise HTTPException(status_code=404, detail="Analyst conversation not found")
    return list_ai_messages(conversation_id)


@app.get(f"{settings.api_prefix}/content/assets")
def get_generated_assets(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    assets = list_items("generated_assets")
    return [asset for asset in assets if (not event_id or asset.get("event_id") == event_id) and (not session_id or asset.get("session_id") == session_id)]


@app.get(f"{settings.api_prefix}/content/assets/{{asset_id}}/export", response_model=None)
def export_content_asset(asset_id: str, format: str = "markdown"):
    asset = next((row for row in list_items("generated_assets") if row.get("id") == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail="Content asset not found")
    normalized = format.lower()
    if normalized not in {"markdown", "json", "txt"}:
        raise HTTPException(status_code=400, detail="Asset export format must be markdown, json, or txt")
    content = asset.get("content") if isinstance(asset.get("content"), dict) else {"output": asset.get("content", "")}
    title = asset.get("title") or "Generated event asset"
    safe_name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "event-asset"
    filename = f"{safe_name}.{normalized if normalized != 'markdown' else 'md'}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if normalized == "json":
        return JSONResponse(content=asset, headers=headers)
    output = str(content.get("output") or content.get("body") or "").strip()
    if normalized == "txt":
        return PlainTextResponse(output, media_type="text/plain", headers=headers)
    body = f"# {title}\n\n- **Type:** {asset.get('asset_type', 'content')}\n- **Status:** {asset.get('status', 'draft')}\n\n{output}\n"
    return PlainTextResponse(body, media_type="text/markdown", headers=headers)


@app.get(f"{settings.api_prefix}/reports")
def get_reports(event_id: str | None = None) -> list[dict]:
    return list_reports(event_id)


@app.post(f"{settings.api_prefix}/reports/generate", status_code=201)
def generate_report(request: ReportGenerateRequest) -> dict:
    snapshot = event_intelligence(request.event_id)
    allowed = set(request.session_ids)
    sessions = [row for row in snapshot.get("sessions", []) if not allowed or row.get("session_id") in allowed]
    if request.session_ids and not sessions:
        raise HTTPException(status_code=400, detail="No selected sessions belong to this event")
    sections = request.sections or ["overview", "themes", "sessions", "takeaways", "evidence"]
    content = {"report_type": request.report_type, "targetLanguage": request.targetLanguage, "sections": sections, "overview": {"event": (snapshot.get("event") or {}).get("name", "Event"), "source_counts": snapshot.get("source_counts", {})}}
    if "themes" in sections:
        content["themes"] = [{"label": topic.get("label"), "count": topic.get("count"), "evidence": topic.get("evidence", [])[:3]} for topic in snapshot.get("themes", [])[:15]]
        content["cross_session_themes"] = snapshot.get("cross_session_themes", [])[:15]
    if "sessions" in sections:
        content["sessions"] = sessions
    if "takeaways" in sections:
        content["takeaways"] = snapshot.get("takeaways", [])
    if "evidence" in sections:
        content["evidence"] = [evidence for session in sessions for evidence in session.get("evidence", [])][:30]
    report = create_report({"event_id": request.event_id, "source_session_ids": [row.get("session_id") for row in sessions], "report_type": request.report_type, "title": request.title, "format": request.format, "content": content})
    return {"mode": snapshot.get("mode", "grounded-local"), "report": report}


@app.get(f"{settings.api_prefix}/reports/{{report_id}}/export", response_model=None)
def export_report(report_id: str, format: str = "markdown"):
    report = next((row for row in list_items("reports") if row.get("id") == report_id), None)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if format.lower() == "json":
        return JSONResponse(content=report)
    content = report.get("content") or {}
    lines = [f"# {report.get('title', 'Event report')}", "", f"Report type: {report.get('report_type', 'executive_brief')}", ""]
    overview = content.get("overview") or {}
    if overview: lines.extend(["## Overview", f"Event: {overview.get('event', 'Event')}", f"Sources: {overview.get('source_counts', {})}", ""])
    if content.get("themes"): lines.extend(["## Themes", *[f"- {row.get('label')}: {row.get('count')} signals" for row in content["themes"]], ""])
    if content.get("takeaways"): lines.extend(["## Takeaways", *[f"- {row.get('title')}: {row.get('body')}" for row in content["takeaways"]], ""])
    if content.get("evidence"): lines.extend(["## Evidence", *[f"- {row.get('snippet')}" for row in content["evidence"]], ""])
    return PlainTextResponse("\n".join(lines), media_type="text/markdown", headers={"Content-Disposition": f"attachment; filename={report_id}.md"})


@app.get(f"{settings.api_prefix}/content/assets/{{asset_id}}/versions")
def get_asset_versions(asset_id: str) -> list[dict]:
    return list_asset_versions(asset_id)


@app.patch(f"{settings.api_prefix}/content/assets/{{asset_id}}")
def edit_generated_asset(asset_id: str, request: AssetUpdateRequest) -> dict:
    try:
        return update_generated_asset(asset_id, request.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/content/assets/{{asset_id}}/rewrite")
def rewrite_generated_asset(asset_id: str, request: AssetRewriteRequest) -> dict:
    instruction = request.instruction.strip()
    if len(instruction) < 3:
        raise HTTPException(status_code=400, detail="Describe the change you want to make")
    asset = next((row for row in list_items("generated_assets") if row.get("id") == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail="Content asset not found")
    content = asset.get("content") if isinstance(asset.get("content"), dict) else {"output": asset.get("content", "")}
    current = str(content.get("output") or "").strip()
    if not current:
        raise HTTPException(status_code=400, detail="This asset has no editable content")
    try:
        result = rewrite_content_ai(current, instruction, request.targetLanguage, asset.get("asset_type", "attendee_recap"))
        mode = "ai"
    except Exception:
        lowered = instruction.lower()
        output = current
        if "short" in lowered or "brief" in lowered:
            output = current[:900].rstrip() + ("…" if len(current) > 900 else "")
        if "professional" in lowered or "executive" in lowered:
            output = output.replace("Key signal", "Key event signal").replace("Next action", "Recommended next action")
        result = {"model": "local-fallback", "targetLanguage": request.targetLanguage, "assetType": asset.get("asset_type", "attendee_recap"), "output": output}
        mode = "fallback"
    updated = update_generated_asset(asset_id, {"content": {**content, "output": result.get("output", ""), "model": result.get("model", ""), "targetLanguage": request.targetLanguage}, "status": "draft"})
    return {**result, "mode": mode, "asset": updated, "instruction": instruction}


@app.get(f"{settings.api_prefix}/takeaways")
def get_takeaways(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    rows = list_items("takeaways")
    return [row for row in rows if (not event_id or row.get("event_id") == event_id) and (not session_id or row.get("session_id") == session_id)]


@app.post(f"{settings.api_prefix}/takeaways/generate", status_code=201)
def generate_takeaway(request: TakeawayGenerateRequest) -> dict:
    source_rows = [row for row in (list_items("transcripts") + list_items("insights")) if (not request.event_id or row.get("event_id") == request.event_id) and (not request.session_id or row.get("session_id") == request.session_id)]
    source = request.text.strip() or "\n".join(row.get("text") or f"{row.get('title', '')}: {row.get('body', '')}" for row in source_rows)
    if len(source.strip()) < 10:
        raise HTTPException(status_code=400, detail="Capture more event evidence before generating a takeaway")
    try:
        result = summarize(source, request.targetLanguage)
        mode = "ai"
    except Exception:
        result = {"model": "local-fallback", "targetLanguage": request.targetLanguage, "output": f"Key takeaway\n{source[:360]}\n\nNext action\nReview this signal with the event team and turn it into an approved follow-up."}
        mode = "fallback"
    takeaway = create_takeaway({"event_id": request.event_id, "session_id": request.session_id, "title": request.title, "body": result.get("output", ""), "confidence": .9 if mode == "ai" else .72, "evidence": [{"id": row.get("id"), "session_id": row.get("session_id")} for row in source_rows[:12]]})
    return {"mode": mode, "takeaway": takeaway, "model": result.get("model"), "targetLanguage": request.targetLanguage}


@app.post(f"{settings.api_prefix}/content/generate", status_code=201)
def generate_content(request: ContentGenerateRequest) -> dict:
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Source text is required")
    try:
        result = generate_content_ai(request.text, request.targetLanguage, request.asset_type)
        mode = "ai"
    except Exception:
        fallback_titles = {"executive_brief": "Executive brief", "attendee_recap": "Attendee recap", "speaker_pack": "Speaker pack", "social_carousel": "Social carousel", "followup_email": "Follow-up email", "sponsor_update": "Sponsor update"}
        result = {"model": "local-fallback", "targetLanguage": request.targetLanguage, "assetType": request.asset_type, "output": f"{fallback_titles.get(request.asset_type, 'Event content')}\n\nKey signal\n{request.text[:420]}\n\nNext action\nShare this evidence with the event team and approve the final version before publishing."}
        mode = "fallback"
    asset = create_generated_asset({"event_id": request.event_id, "session_id": request.session_id, "asset_type": request.asset_type, "title": request.title, "content": {"output": result.get("output", ""), "targetLanguage": request.targetLanguage, "model": result.get("model", "")}, "status": "draft"})
    return {**result, "mode": mode, "asset": asset}


@app.post(f"{settings.api_prefix}/capture/text", status_code=201)
def capture_text(request: CaptureRequest) -> dict:
    """Accept a live text chunk from a browser, mixer bridge, or meeting bot."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Capture text is required")
    payload = {"text": text, "model": "manual-capture", "language": request.language, "session_id": request.session_id, "speaker": request.speaker}
    saved = create_transcript(payload)
    insight = create_insight(payload)
    return {"transcript": saved, "insight": insight, "captured": True, "mode": storage_mode()}


@app.websocket(f"{settings.api_prefix}/ws/capture/{{session_id}}")
async def capture_socket(websocket: WebSocket, session_id: str) -> None:
    """Accept authenticated live text chunks from a venue bridge or meeting integration."""
    await websocket.accept()
    if not await _websocket_authenticated(websocket):
        await websocket.close(code=1008, reason="Organizer authentication is required")
        return
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            text = str(payload.get("text", "")).strip()
            if not text:
                await websocket.send_json({"type": "error", "detail": "Capture text is required"})
                continue
            capture = {"text": text, "model": payload.get("model", "live-bridge"), "language": payload.get("language", "auto"), "session_id": session_id, "speaker": payload.get("speaker", "Live speaker")}
            saved = create_transcript(capture)
            insight = create_insight(capture)
            await websocket.send_json({"type": "capture", "transcript": saved, "insight": insight, "mode": storage_mode()})
    except WebSocketDisconnect:
        return


@app.post(f"{settings.api_prefix}/ai/summarize")
def create_summary(request: SummaryRequest) -> dict:
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Transcript text is required")
    try:
        return summarize(request.text, request.targetLanguage)
    except Exception:
        source = request.text.strip().replace("\n", " ")
        return {"model": "local-fallback", "targetLanguage": request.targetLanguage, "mode": "fallback", "output": f"Summary\n{source[:360]}\n\nKey signals\n• Capture the strongest themes from the session.\n• Turn the next action into a clear attendee takeaway.\n\nAction items\n• Review the source transcript with the event team.\n• Approve the best quote before publishing.\n\nSocial post\nA strong event moment is ready to carry forward: {source[:180]}"}


@app.post(f"{settings.api_prefix}/ai/translate")
def create_translation(request: TranslateRequest) -> dict:
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Transcript text is required")
    try:
        return translate(request.text, request.targetLanguage)
    except Exception:
        return {"model": "local-fallback", "targetLanguage": request.targetLanguage, "mode": "fallback", "output": f"[{request.targetLanguage}] {request.text.strip()}"}


@app.post(f"{settings.api_prefix}/transcription/batch")
async def transcribe_audio(file: UploadFile = File(...), session_id: str = Form("ses-001"), language: str = Form("auto")) -> dict:
    try:
        result = transcribe(await file.read(), file.content_type or "audio/webm", [] if language == "auto" else [language])
        capture = {"text": result["transcript"], "model": result["model"], "language": language, "session_id": session_id, "speaker": "Detected speaker"}
        saved = create_transcript(capture)
        insight = create_insight(capture)
        return {**result, "transcript": saved, "insight": insight}
    except Exception as exc: raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled_error(_, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": "The server could not complete that request"})


@app.get("/attendee/{slug}", include_in_schema=False)
def attendee_slug_redirect(slug: str) -> RedirectResponse:
    """Resolve legacy/event-slug links into the tokenized public portal URL."""
    normalized = slug.strip().lower()
    link = next((item for item in list_items("share_links") if str(item.get("destination", "")).rstrip("/").split("/")[-1].lower() == normalized), None)
    if not link:
        link = next((item for item in list_items("share_links") if str(item.get("token", "")).lower().startswith(normalized)), None)
    if not link:
        return RedirectResponse(url="/#landing", status_code=307)
    token = link.get("token")
    return RedirectResponse(url=f"/?share={token}#attendee", status_code=307)


app.mount("/", StaticFiles(directory=Path(FRONTEND_DIR), html=True), name="frontend")
