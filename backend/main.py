# Smart Event Manager API entrypoint.
# Transcript segment persistence is covered by the normalized API routes below.
# Smoke checks use the in-memory demo store and are reset on reload.
# Event intelligence is exposed through the shared /api/intelligence endpoint.
# Attendee portal routes remain usable without organizer credentials.
# Transcript edits preserve a revision trail for organizer review.
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import re
import uuid
import logging
import hashlib
import hmac
import secrets
import time
import base64
import json
from html import escape as html_escape
from typing import Any
import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import FRONTEND_DIR, settings
from .demo_store import demo_store
from .repository import admin_overview, analytics, assign_session_speaker, attendee_matches, create_ai_conversation, create_ai_message, create_attendee, create_event, create_feedback, create_file, create_generated_asset, create_insight, create_invitation, create_organization, create_poll, create_processing_job, create_question, create_report, create_session, create_speaker, create_takeaway, create_transcript, create_translation as persist_translation, dashboard, delete_event, delete_file, delete_session, delete_speaker, duplicate_session, ensure_share_link, event_intelligence, get_attendee_preferences, get_brand_kit, get_share_link, list_ai_conversations, list_ai_messages, list_asset_versions, list_integrations, list_items, list_reports, list_session_speakers, list_summaries, list_transcript_segment_revisions, list_transcript_segments, moderate_question, remove_team_member, respond_poll, save_summary, search_knowledge, set_attendee_checkin, set_session_status, share_link_allows, storage_mode, team_workspace, topic_cloud, unassign_session_speaker, update_event, update_file, update_generated_asset, update_organization, update_poll, update_processing_job, update_session, update_speaker, update_team_member, update_transcript_segment, upsert_attendee_preferences, upsert_brand_kit, upsert_integration, vote_question
from .vertex_ai import analyst_answer, generate_content as generate_content_ai, rewrite_content as rewrite_content_ai, summarize, transcribe, translate
from .storage import download_bytes as download_storage_bytes, upload_bytes as upload_storage_bytes, delete_object as delete_storage_object, signed_url as create_storage_signed_url

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
    session_id: str | None = None


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


class AttendeePreferenceRequest(BaseModel):
    attendee_id: str | None = None
    event_id: str
    share_token: str | None = None
    full_name: str = "Anonymous attendee"
    email: str = ""
    role: str = ""
    industry: str = ""
    interests: list[str] = []
    language: str = "en"


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


class OrganizationRequest(BaseModel):
    name: str
    slug: str | None = None
    logo_url: str = ""
    website: str = ""
    industry: str = ""
    timezone: str = "UTC"
    preferred_language: str = "en"


class OnboardingRequest(BaseModel):
    organization_name: str = "My event team"
    event_name: str = "My first event"
    venue: str = ""
    timezone: str = "UTC"


class IntegrationRequest(BaseModel):
    organization_id: str = "org-demo"
    provider: str = ""
    status: str = "disconnected"
    display_name: str = ""
    metadata: dict = {}


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
        (relative == "public/assets" and method == "GET")
        or relative.startswith("public/share")
        or (relative == "questions" and method in {"GET", "POST"})
        or (relative.startswith("questions/") and relative.endswith("/votes") and method == "POST")
        or (relative == "polls" and method == "GET")
        or (relative == "poll-responses" and method == "POST")
        or (relative == "feedback" and method == "POST")
      or (relative == "transcripts" and method == "GET")
      or (relative == "summaries" and method == "GET")
      or (relative.startswith("sessions/") and relative.endswith("/summary") and method == "POST")
      or (relative == "transcript-segments" and method == "GET")
        or (relative.startswith("transcript-segments/") and method in {"GET", "POST"} and (relative.endswith("/translate") or relative.endswith("/translations")))
      or (relative == "topics" and method == "GET")
      or (relative == "takeaways" and method == "GET")
      or (relative == "search" and method == "GET")
      or (relative == "ai/translate" and method == "POST")
      or (relative == "attendee/preferences" and method in {"GET", "POST"})
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
    if relative.startswith("admin/") and role != "super_admin":
        return JSONResponse(status_code=403, content={"detail": "Super admin access is required"})
    if relative.startswith("organizations") and role not in {"super_admin", "organization_admin"}:
        return JSONResponse(status_code=403, content={"detail": "Organization admin access is required"})
    if relative.startswith("integrations") and role not in {"super_admin", "organization_admin"}:
        return JSONResponse(status_code=403, content={"detail": "Organization admin access is required"})
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
    return {"ok": True, "build": "20260928-vector-search", "mode": storage_mode(), "vertexConfigured": vertex_ready, "geminiConfigured": bool(settings.gemini_api_key), "aiConfigured": vertex_ready or bool(settings.gemini_api_key), "storageConfigured": bool(settings.supabase_url and settings.supabase_storage_key), "storageBucket": settings.supabase_storage_bucket if settings.supabase_url and settings.supabase_storage_key else None, "project": settings.project or None, "models": {"text": settings.text_model, "batch": settings.batch_model, "live": settings.live_model}}


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
    payload = response.json()
    session = payload.get("session") if isinstance(payload, dict) else None
    if not session or not session.get("access_token"):
        return {"mode": "supabase", "session": None, "requires_verification": True, "message": "Account created. Check your email to verify the account before logging in."}
    return {"mode": "supabase", "session": session}


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


def _supabase_user_from_authorization(authorization: str | None) -> dict:
    if not authorization or not settings.supabase_url or not settings.supabase_anon_key:
        return {}
    response = httpx.get(
        f"{settings.supabase_url}/auth/v1/user",
        headers={"apikey": settings.supabase_anon_key, "Authorization": authorization},
        timeout=20,
    )
    if not response.is_success:
        raise HTTPException(status_code=401, detail="Organizer session is invalid or expired")
    return response.json()


def _supabase_rest_insert(table: str, payload: dict) -> dict:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Supabase service-role configuration is required for workspace setup")
    response = httpx.post(
        f"{settings.supabase_url}/rest/v1/{table}",
        headers={"apikey": settings.supabase_service_role_key, "Authorization": f"Bearer {settings.supabase_service_role_key}", "Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
        timeout=20,
    )
    if not response.is_success:
        detail = response.json().get("message") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise HTTPException(status_code=502, detail=f"Supabase could not create {table}: {detail or 'request failed'}")
    rows = response.json()
    return rows[0] if rows else payload


@app.post(f"{settings.api_prefix}/onboarding/bootstrap", status_code=201)
def bootstrap_workspace(request: OnboardingRequest, authorization: str | None = Header(default=None)) -> dict:
    """Create an organizer-owned starter workspace once, then return its portal link."""
    organization_name = request.organization_name.strip() or "My event team"
    event_name = request.event_name.strip() or "My first event"
    if not settings.supabase_url:
        existing_event = next(iter(list_items("events")), None)
        if existing_event:
            return dashboard(existing_event.get("id"))
        organization = create_organization({"name": organization_name, "timezone": request.timezone})
        event = create_event({"organization_id": organization["id"], "name": event_name, "venue": request.venue, "status": "draft", "brand_color": "#7568f3"})
        session = create_session({"event_id": event["id"], "title": "Opening session", "track": "Main stage", "room": request.venue or "Main room"})
        link = ensure_share_link(event["id"], session["id"])
        return {"created": True, "organization": organization, "event": event, "session": session, "share_links": [link]}

    user = _supabase_user_from_authorization(authorization)
    user_id = user.get("id")
    email = (user.get("email") or "").strip().lower()
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Authenticated organizer identity is required")
    service_headers = {"apikey": settings.supabase_service_role_key, "Authorization": f"Bearer {settings.supabase_service_role_key}"}
    members = httpx.get(f"{settings.supabase_url}/rest/v1/organization_members?user_id=eq.{user_id}&status=eq.active&select=organization_id,role&limit=1", headers=service_headers, timeout=20)
    if members.is_success and members.json():
        organization_id = members.json()[0].get("organization_id")
        events = httpx.get(f"{settings.supabase_url}/rest/v1/events?organization_id=eq.{organization_id}&select=*&order=created_at.asc&limit=1", headers=service_headers, timeout=20)
        if events.is_success and events.json():
            return dashboard(events.json()[0].get("id"))
    profile = httpx.get(f"{settings.supabase_url}/rest/v1/profiles?id=eq.{user_id}&select=id&limit=1", headers=service_headers, timeout=20)
    if profile.is_success and not profile.json():
        _supabase_rest_insert("profiles", {"id": user_id, "email": email, "full_name": email.split("@", 1)[0]})
    slug_base = re.sub(r"[^a-z0-9]+", "-", organization_name.lower()).strip("-") or "event-team"
    organization = _supabase_rest_insert("organizations", {"name": organization_name, "slug": f"{slug_base}-{uuid.uuid4().hex[:8]}", "timezone": request.timezone})
    _supabase_rest_insert("organization_members", {"organization_id": organization["id"], "user_id": user_id, "role": "organization_admin", "status": "active"})
    event = _supabase_rest_insert("events", {"organization_id": organization["id"], "title": event_name, "venue": request.venue, "status": "draft", "created_by": user_id})
    session = _supabase_rest_insert("sessions", {"event_id": event["id"], "title": "Opening session", "track": "Main stage", "room": request.venue or "Main room", "status": "scheduled"})
    share = _supabase_rest_insert("share_links", {"event_id": event["id"], "session_id": session["id"], "label": "Attendee portal", "token": f"{organization['slug']}-live", "destination": f"/attendee/{organization['slug']}", "clicks": 0})
    return {"created": True, "organization": organization, "event": {**event, "name": event.get("name") or event.get("title")}, "session": session, "share_links": [share]}


@app.get(f"{settings.api_prefix}/organizations")
def get_organizations() -> list[dict]:
    return list_items("organizations")


@app.post(f"{settings.api_prefix}/organizations", status_code=201)
def add_organization(request: OrganizationRequest) -> dict:
    try:
        return create_organization(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch(f"{settings.api_prefix}/organizations/{{organization_id}}")
def edit_organization(organization_id: str, request: OrganizationRequest) -> dict:
    try:
        return update_organization(organization_id, request.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/integrations")
def get_integrations(organization_id: str | None = None) -> list[dict]:
    return list_integrations(organization_id)


@app.put(f"{settings.api_prefix}/integrations/{{provider}}")
def save_integration(provider: str, request: IntegrationRequest) -> dict:
    try:
        return upsert_integration({**request.model_dump(), "provider": provider})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/admin/overview")
def get_admin_overview() -> dict:
    return admin_overview()


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


@app.delete(f"{settings.api_prefix}/team/members/{{member_id}}")
def remove_team_member_route(member_id: str) -> dict:
    try: return remove_team_member(member_id)
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
def get_share_links(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    if event_id:
        try: return [ensure_share_link(event_id, session_id)]
        except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list_items("share_links")


@app.get(f"{settings.api_prefix}/public/share/{{token}}")
def get_public_share(token: str) -> dict:
    result = get_share_link(token)
    if not result:
        raise HTTPException(status_code=404, detail="Share link not found")
    return result


@app.get(f"{settings.api_prefix}/public/assets")
def get_public_assets(event_id: str | None = None, session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id, event_id=event_id)
    return [
        row for row in list_items("generated_assets")
        if row.get("status") == "published"
        and (not event_id or row.get("event_id") == event_id)
        and (not session_id or row.get("session_id") in {None, session_id})
    ]


def _require_portal_access(authorization: str | None, share_token: str | None, session_id: str | None = None, event_id: str | None = None) -> None:
    if authorization:
        return
    if not share_link_allows(share_token, session_id=session_id, event_id=event_id):
        raise HTTPException(status_code=403, detail="A valid attendee share link is required")


@app.get(f"{settings.api_prefix}/transcripts")
def get_transcripts(session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id)
    rows = list_items("transcripts")
    return [row for row in rows if not session_id or row.get("session_id") == session_id]


@app.get(f"{settings.api_prefix}/summaries")
def get_summaries(session_id: str | None = None, event_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id, event_id=event_id)
    return list_summaries(session_id=session_id, event_id=event_id)


@app.post(f"{settings.api_prefix}/sessions/{{session_id}}/summary", status_code=201)
def generate_session_summary(session_id: str, request: SessionSummaryRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    session = next((row for row in list_items("sessions") if row.get("id") == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _require_portal_access(authorization, share_token, session_id=session_id, event_id=session.get("event_id"))
    rows = [row for row in list_items("transcripts") if row.get("session_id") == session_id]
    source = "\n".join(row.get("text", "") for row in reversed(rows)).strip()
    if not source:
        raise HTTPException(status_code=400, detail="Capture transcript evidence before generating a summary")
    try:
        result = summarize(source, request.targetLanguage)
        mode = "ai"
    except Exception:
        result = {"output": f"Overview\n{source[:360]}\n\nMain Discussion Points\n• {source[:240]}\n\nImportant Insights\n• Not captured\n\nDecisions\n• Not captured\n\nRecommendations\n• Review this evidence with the event team.\n\nQuestions Raised\n• Not captured\n\nAction Items\n• Review this evidence with the event team.\n\nNotable Quotes\n• Not captured\n\nTopics\n• Not captured\n\nPeople / Organizations Mentioned\n• Not captured", "model": "local-grounded"}
        mode = "fallback"
    content = {
        "output": result.get("output", ""),
        "sections": ["overview", "main_discussion_points", "important_insights", "decisions", "recommendations", "questions_raised", "action_items", "notable_quotes", "topics"],
        "evidence": [{"id": row.get("id"), "speaker": row.get("speaker"), "timestamp": row.get("created_at"), "snippet": (row.get("text") or "")[:240]} for row in rows[:12]],
    }
    saved = save_summary({"event_id": session.get("event_id"), "session_id": session_id, "language": request.targetLanguage, "content": content, "model": result.get("model", "local-grounded")})
    return {"mode": mode, "summary": saved}


@app.get(f"{settings.api_prefix}/transcript-segments")
def get_transcript_segments(session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id)
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
def translate_transcript_segment(segment_id: str, request: TranslationPersistRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    segment = next((item for item in list_transcript_segments() if item.get("id") == segment_id), None)
    if not segment:
        raise HTTPException(status_code=404, detail="Transcript segment not found")
    _require_portal_access(authorization, share_token, session_id=segment.get("session_id"))
    try:
        result = translate(request.text, request.targetLanguage)
    except Exception:
        result = {"output": f"[{request.targetLanguage}] {request.text}", "mode": "fallback", "targetLanguage": request.targetLanguage}
    saved = persist_translation({"transcript_segment_id": segment_id, "language": request.targetLanguage, "text": result.get("output", "")})
    return {**result, "translation": saved}


@app.get(f"{settings.api_prefix}/transcript-segments/{{segment_id}}/translations")
def get_segment_translations(segment_id: str, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    segment = next((item for item in list_transcript_segments() if item.get("id") == segment_id), None)
    _require_portal_access(authorization, share_token, session_id=segment.get("session_id") if segment else None)
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


async def _process_upload(file_record: dict, job: dict, enrichment_job: dict, raw: bytes, language: str, vocabulary: list[str] | None = None) -> None:
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
            result = transcribe(raw, file_record["mime_type"], [] if language == "auto" else [language], vocabulary or [])
            text, model = result.get("transcript", ""), result.get("model", "uploaded-audio")
        if not text.strip():
            raise ValueError("The uploaded file did not contain readable transcript text")
        capture = {"text": text, "model": model, "language": language, "session_id": file_record["session_id"], "speaker": "Uploaded recording"}
        transcript = create_transcript(capture)
        create_insight(capture)
        update_processing_job(job["id"], status="completed", progress=100, error_message=None)
        update_processing_job(enrichment_job["id"], status="running", progress=15, attempts=int(enrichment_job.get("attempts") or 0) + 1)
        try:
            result = summarize(text, language if language != "auto" else "English")
            enrichment_mode = result.get("model", "configured-ai")
            output = result.get("output", "")
        except Exception:
            enrichment_mode = "local-grounded"
            output = f"Overview\n{text[:360]}\n\nMain Discussion Points\n• {text[:240]}\n\nImportant Insights\n• Review the uploaded evidence with the event team.\n\nDecisions\n• Not captured\n\nRecommendations\n• Confirm next actions from this recording.\n\nQuestions Raised\n• Not captured\n\nAction Items\n• Review the transcript and assign an owner.\n\nNotable Quotes\n• Not captured\n\nTopics\n• Not captured\n\nPeople / Organizations Mentioned\n• Not captured"
        update_processing_job(enrichment_job["id"], status="running", progress=65)
        summary = save_summary({"event_id": file_record.get("event_id"), "session_id": file_record["session_id"], "language": language if language != "auto" else "English", "content": {"output": output, "sections": ["overview", "main_discussion_points", "important_insights", "decisions", "recommendations", "questions_raised", "action_items", "notable_quotes", "topics"], "evidence": [{"id": transcript.get("id"), "speaker": transcript.get("speaker"), "timestamp": transcript.get("created_at"), "snippet": text[:240]}]}, "model": enrichment_mode})
        create_takeaway({"event_id": file_record.get("event_id"), "session_id": file_record["session_id"], "title": "Uploaded recording signal", "body": text[:360], "confidence": .74, "evidence": [{"id": transcript.get("id"), "session_id": file_record["session_id"]}]})
        update_processing_job(enrichment_job["id"], status="completed", progress=100, error_message=None)
        update_file(file_record["id"], status="completed")
    except Exception as exc:
        try:
            update_file(file_record["id"], status="failed")
        except (KeyError, ValueError):
            pass
        update_processing_job(job["id"], status="failed", progress=100, error_message=str(exc))
        if enrichment_job:
            update_processing_job(enrichment_job["id"], status="failed", progress=100, error_message=str(exc))


@app.post(f"{settings.api_prefix}/files/upload", status_code=202)
async def upload_media(background_tasks: BackgroundTasks, file: UploadFile = File(...), session_id: str = Form("ses-001"), event_id: str | None = Form(None), language: str = Form("auto"), vocabulary: str = Form("")) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported upload type: {suffix or 'missing extension'}")
    raw = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Upload exceeds the {settings.max_upload_mb} MB limit")
    event_id = event_id or next((item.get("event_id") for item in list_items("sessions") if item.get("id") == session_id), None)
    stored_name = f"{uuid.uuid4().hex}-{_safe_upload_name(file.filename or 'upload') }"
    content_type = file.content_type or "application/octet-stream"
    if storage_mode() == "supabase":
        storage_path = f"events/{event_id or 'unassigned'}/{stored_name}"
        try:
            upload_storage_bytes(storage_path, raw, content_type)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not save upload to Supabase Storage: {exc}") from exc
    else:
        upload_dir = settings.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / stored_name
        destination.write_bytes(raw)
        storage_path = str(destination)
    file_record = create_file({"event_id": event_id, "session_id": session_id, "original_name": file.filename or "upload", "storage_path": storage_path, "mime_type": content_type, "size_bytes": len(raw), "status": "processing"})
    job = create_processing_job({"event_id": event_id, "session_id": session_id, "job_type": "transcription", "status": "queued", "progress": 0})
    enrichment_job = create_processing_job({"event_id": event_id, "session_id": session_id, "job_type": "ai_enrichment", "status": "queued", "progress": 0})
    terms = [item.strip() for item in re.split(r"[,\n]", vocabulary) if item.strip()][:80]
    background_tasks.add_task(_process_upload, file_record, job, enrichment_job, raw, language, terms)
    return {"file": file_record, "job": job, "jobs": [job, enrichment_job], "status": "queued"}


@app.get(f"{settings.api_prefix}/files")
def get_files(event_id: str | None = None, session_id: str | None = None) -> list[dict]:
    return [item for item in list_items("files") if (not event_id or item.get("event_id") == event_id) and (not session_id or item.get("session_id") == session_id)]


@app.get(f"{settings.api_prefix}/files/{{file_id}}/access")
def access_file(file_id: str, expires_in: int = 3600) -> dict:
    file_record = next((item for item in list_items("files") if item.get("id") == file_id), None)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")
    path = file_record.get("storage_path") or ""
    if storage_mode() == "supabase":
        try:
            return {"file": file_record, "url": create_storage_signed_url(path, expires_in)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not create secure file access URL: {exc}") from exc
    local_path = Path(path).resolve()
    upload_root = (settings.data_dir / "uploads").resolve()
    if local_path.parent != upload_root or not local_path.exists():
        raise HTTPException(status_code=404, detail="Stored file is no longer available")
    return {"file": file_record, "url": f"{settings.api_prefix}/files/{file_id}/download"}


@app.get(f"{settings.api_prefix}/files/{{file_id}}/download")
def download_file(file_id: str):
    file_record = next((item for item in list_items("files") if item.get("id") == file_id), None)
    if not file_record or storage_mode() == "supabase":
        raise HTTPException(status_code=404, detail="Local file download is unavailable")
    local_path = Path(file_record.get("storage_path", "")).resolve()
    upload_root = (settings.data_dir / "uploads").resolve()
    if local_path.parent != upload_root or not local_path.exists():
        raise HTTPException(status_code=404, detail="Stored file is no longer available")
    return FileResponse(local_path, media_type=file_record.get("mime_type") or "application/octet-stream", filename=file_record.get("original_name") or "event-upload")


@app.delete(f"{settings.api_prefix}/files/{{file_id}}")
def remove_file(file_id: str) -> dict:
    try:
        result = delete_file(file_id)
        path = result["file"].get("storage_path")
        if storage_mode() == "supabase" and path:
            delete_storage_object(path)
        elif path:
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


@app.post(f"{settings.api_prefix}/processing-jobs/{{job_id}}/retry", status_code=202)
def retry_processing_job(job_id: str, background_tasks: BackgroundTasks) -> dict:
    """Requeue the most recent stored upload for a failed processing job."""
    job = next((item for item in list_items("processing_jobs") if item.get("id") == job_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if job.get("status") != "failed":
        raise HTTPException(status_code=409, detail="Only failed processing jobs can be retried")
    files = [item for item in list_items("files") if item.get("session_id") == job.get("session_id") and item.get("storage_path")]
    files.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    file_record = files[0] if files else None
    if not file_record:
        raise HTTPException(status_code=404, detail="The source upload for this job is no longer available")
    try:
        if storage_mode() == "supabase":
            raw = download_storage_bytes(file_record["storage_path"])
        else:
            raw = Path(file_record["storage_path"]).read_bytes()
    except (OSError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not read the stored upload: {exc}") from exc
    try:
        update_file(file_record["id"], status="processing")
        queued = update_processing_job(job_id, status="queued", progress=0, error_message=None, attempts=int(job.get("attempts") or 0) + 1)
        enrichment = next((item for item in list_items("processing_jobs") if item.get("session_id") == job.get("session_id") and item.get("job_type") == "ai_enrichment" and item.get("status") == "failed"), None)
        if enrichment:
            enrichment = update_processing_job(enrichment["id"], status="queued", progress=0, error_message=None, attempts=int(enrichment.get("attempts") or 0) + 1)
        else:
            enrichment = create_processing_job({"event_id": job.get("event_id"), "session_id": job.get("session_id"), "job_type": "ai_enrichment", "status": "queued", "progress": 0})
        background_tasks.add_task(_process_upload, file_record, queued, enrichment, raw, "auto", [])
        return {"status": "queued", "file": file_record, "jobs": [queued, enrichment]}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/questions")
def get_questions(session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id)
    questions = list_items("questions")
    return [item for item in questions if not session_id or item.get("session_id") == session_id]


@app.post(f"{settings.api_prefix}/questions", status_code=201)
def add_question(request: QuestionRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    _require_portal_access(authorization, share_token, session_id=request.session_id)
    if len(request.body.strip()) < 3: raise HTTPException(status_code=400, detail="Question is too short")
    return create_question(request.model_dump())


@app.post(f"{settings.api_prefix}/questions/{{question_id}}/votes")
def add_question_vote(question_id: str, request: VoteRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    question = next((item for item in list_items("questions") if item.get("id") == question_id), None)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    _require_portal_access(authorization, share_token, session_id=question.get("session_id"))
    try: return vote_question(question_id, request.voter_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch(f"{settings.api_prefix}/questions/{{question_id}}")
def moderate_question_route(question_id: str, request: QuestionModerationRequest) -> dict:
    try: return moderate_question(question_id, request.status, request.pinned)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/polls")
def get_polls(session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id)
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
def add_poll_response(request: PollResponseRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    poll = next((item for item in list_items("polls") if item.get("id") == request.poll_id), None)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    _require_portal_access(authorization, share_token, session_id=poll.get("session_id"))
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
def add_feedback(request: FeedbackRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    _require_portal_access(authorization, share_token, session_id=request.session_id)
    for rating in (request.speaker_rating, request.content_rating, request.session_rating):
        if rating is not None and not 1 <= rating <= 5: raise HTTPException(status_code=400, detail="Ratings must be between 1 and 5")
    return create_feedback(request.model_dump())


def _attendee_recommendations(event_id: str, preferences: dict) -> dict:
    interests = {str(item).strip().lower() for item in (preferences.get("interests") or []) if str(item).strip()}
    profile_terms = interests | {str(preferences.get("role") or "").lower(), str(preferences.get("industry") or "").lower()}
    sessions = [row for row in list_items("sessions") if row.get("event_id") == event_id]
    ranked = []
    for session in sessions:
        searchable = " ".join(str(session.get(key) or "") for key in ("title", "track", "room", "speaker", "summary", "tags")).lower()
        matched = sorted(term for term in profile_terms if term and len(term) > 2 and term in searchable)
        ranked.append({"session": session, "score": len(matched), "matched_interests": matched})
    ranked.sort(key=lambda item: (item["score"], item["session"].get("starts_at") or ""), reverse=True)
    return {"preferences": preferences, "recommendations": ranked[:6]}


@app.post(f"{settings.api_prefix}/attendee/preferences", status_code=201)
def save_attendee_preferences(request: AttendeePreferenceRequest) -> dict:
    _require_portal_access(None, request.share_token, event_id=request.event_id)
    attendee_id = request.attendee_id
    if not attendee_id:
        attendee = create_attendee({"event_id": request.event_id, "full_name": request.full_name, "email": request.email})
        attendee_id = attendee["id"]
    preferences = upsert_attendee_preferences({**request.model_dump(), "attendee_id": attendee_id})
    return {"attendee_id": attendee_id, **_attendee_recommendations(request.event_id, preferences)}


@app.get(f"{settings.api_prefix}/attendee/preferences")
def read_attendee_preferences(event_id: str, attendee_id: str, share_token: str | None = None) -> dict:
    _require_portal_access(None, share_token, event_id=event_id)
    preferences = get_attendee_preferences(attendee_id)
    if not preferences:
        raise HTTPException(status_code=404, detail="Attendee preferences not found")
    return {"attendee_id": attendee_id, **_attendee_recommendations(event_id, preferences)}


@app.get(f"{settings.api_prefix}/analytics")
def get_analytics(event_id: str = "evt-001") -> dict: return analytics(event_id)


@app.get(f"{settings.api_prefix}/intelligence")
def get_event_intelligence(event_id: str | None = None) -> dict:
    return event_intelligence(event_id)


@app.get(f"{settings.api_prefix}/search")
def search(query: str, event_id: str | None = None, session_id: str | None = None, speaker: str | None = None, source_type: str | None = None, language: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    if len(query.strip()) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least two characters")
    _require_portal_access(authorization, share_token, event_id=event_id)
    return search_knowledge(query, event_id, session_id=session_id, speaker=speaker, source_type=source_type, language=language)


@app.get(f"{settings.api_prefix}/topics")
def get_topics(event_id: str | None = None, session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id, event_id=event_id)
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


def _slide_deck_html(title: str, output: str, primary: str = "#7568f3", accent: str = "#e4ff63") -> str:
    """Turn a grounded presentation outline into a self-contained browser deck."""
    sections: list[dict[str, list[str] | str]] = []
    current: dict[str, list[str] | str] = {"title": title, "lines": []}
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading:
            if current["lines"] or not sections:
                sections.append(current)
            current = {"title": heading.group(1).strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"] or not sections:
        sections.append(current)
    slides = []
    for index, section in enumerate(sections[:24], 1):
        items = section["lines"]
        markup = []
        for item in items:
            if re.match(r"^(?:[-*•]|\d+[.)])\s+", item):
                markup.append(f"<li>{html_escape(re.sub(r'^(?:[-*•]|\\d+[.)])\\s+', '', item))}</li>")
            else:
                markup.append(f"<p>{html_escape(item)}</p>")
        body = f"<ul>{''.join(markup)}</ul>" if any(item.startswith("<li>") for item in markup) else "".join(markup)
        slides.append(f"<article class='slide' data-slide='{index}'><span class='number'>{index:02d}</span><h2>{html_escape(str(section['title']))}</h2><div class='body'>{body}</div></article>")
    safe_title = html_escape(title)
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{safe_title} · presentation</title><style>:root{{--primary:{html_escape(primary)};--accent:{html_escape(accent)};--ink:#171827;--paper:#f4f3ef}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:18px/1.55 Inter,system-ui,sans-serif}}.deck{{max-width:1180px;margin:auto;padding:24px}}header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}.brand{{font-weight:900;letter-spacing:-.04em}}.controls{{display:flex;gap:8px}}button{{border:0;border-radius:999px;background:var(--ink);color:#fff;padding:10px 16px;font-weight:800;cursor:pointer}}button:hover{{background:var(--primary)}}.slide{{display:none;min-height:calc(100vh - 150px);border-radius:28px;padding:clamp(36px,8vw,110px);background:white;box-shadow:0 20px 70px #17182718;position:relative;overflow:hidden}}.slide.active{{display:block}}.slide:after{{content:'';position:absolute;width:360px;height:360px;border-radius:50%;right:-120px;bottom:-140px;background:var(--primary);opacity:.12}}.number{{color:var(--primary);font-weight:900;letter-spacing:.14em}}h2{{font-size:clamp(42px,7vw,92px);line-height:.98;letter-spacing:-.08em;max-width:900px;margin:26px 0 42px}}.body{{max-width:800px;font-size:clamp(20px,2.5vw,30px);color:#555a6d}}.body ul{{padding-left:1.2em}}.body li{{margin:12px 0}}footer{{padding:16px 0;color:#777;font-size:13px}}@media print{{.controls,header,footer{{display:none}}.slide,.slide.active{{display:block;page-break-after:always;min-height:100vh;box-shadow:none}}.deck{{padding:0}}}}</style></head><body><div class='deck'><header><div class='brand'>Smart Event Manager · {safe_title}</div><div class='controls'><button onclick='previousSlide()'>← Previous</button><button onclick='nextSlide()'>Next →</button><button onclick='window.print()'>Print / PDF</button></div></header><main>{''.join(slides)}</main><footer><span id='counter'></span> · Use Print / PDF to save a shareable deck.</footer></div><script>const slides=[...document.querySelectorAll('.slide')];let current=0;function show(index){{current=(index+slides.length)%slides.length;slides.forEach((slide,i)=>slide.classList.toggle('active',i===current));document.querySelector('#counter').textContent=`Slide ${{current+1}} of ${{slides.length}}`;}}function nextSlide(){{show(current+1)}}function previousSlide(){{show(current-1)}}document.addEventListener('keydown',event=>{{if(event.key==='ArrowRight')nextSlide();if(event.key==='ArrowLeft')previousSlide();}});show(0);</script></body></html>"


def _pptx_bytes(title: str, output: str, primary: str = "7568f3") -> bytes:
    """Create a minimal native PPTX from the same grounded outline used by HTML slides."""
    sections: list[dict[str, list[str] | str]] = []
    current: dict[str, list[str] | str] = {"title": title, "lines": []}
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading:
            if current["lines"] or not sections:
                sections.append(current)
            current = {"title": heading.group(1).strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"] or not sections:
        sections.append(current)
    def esc_xml(value: object) -> str:
        return html_escape(str(value), quote=False)
    rgb = primary.lstrip("#")[:6].ljust(6, "7")
    slide_xml = []
    for index, section in enumerate(sections[:24], 1):
        body_paragraphs = "".join(f"<a:p><a:r><a:rPr lang=\"en-US\" sz=\"2200\"/><a:t>{esc_xml(re.sub(r'^(?:[-*•]|\\d+[.)])\\s+', '• ', item))}</a:t></a:r><a:endParaRPr lang=\"en-US\"/></a:p>" for item in section["lines"][:12]) or '<a:p><a:endParaRPr lang="en-US"/></a:p>'
        slide_xml.append(f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="10972800" cy="1371600"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="4000" b="1"/><a:t>{esc_xml(section["title"])}</a:t></a:r><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="2743200"/><a:ext cx="10972800" cy="3657600"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>{body_paragraphs}</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>''')
    count = len(slide_xml)
    content_types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>' + ''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, count + 1)) + '</Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>'
    presentation = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>{"".join(f"<p:sldId id=\"{255+i}\" r:id=\"rId{i+2}\"/>" for i in range(1, count+1))}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    presentation_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>' + ''.join(f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in range(1, count + 1)) + '</Relationships>'
    master = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="Master"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles/></p:sldMaster>'
    layout = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    master_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'
    layout_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'
    theme = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Smart Event Manager"><a:themeElements><a:clrScheme name="Smart"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="171827"/></a:dk2><a:lt2><a:srgbClr val="F8F7F3"/></a:lt2><a:accent1><a:srgbClr val="' + rgb + '"/></a:accent1><a:accent2><a:srgbClr val="E4FF63"/></a:accent2></a:clrScheme><a:fontScheme name="Smart"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme><a:fmtScheme name="Smart"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        package.writestr("ppt/slideMasters/slideMaster1.xml", master)
        package.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        package.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        package.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        package.writestr("ppt/theme/theme1.xml", theme)
        for index, slide in enumerate(slide_xml, 1):
            package.writestr(f"ppt/slides/slide{index}.xml", slide)
            package.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')
    return buffer.getvalue()


def _docx_bytes(title: str, output: str) -> bytes:
    """Create a lightweight native DOCX from grounded generated content."""
    def esc_xml(value: object) -> str:
        return html_escape(str(value), quote=False)
    paragraphs = [f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{esc_xml(title)}</w:t></w:r></w:p>']
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading:
            paragraphs.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{esc_xml(heading.group(1))}</w:t></w:r></w:p>')
        else:
            bullet = bool(re.match(r"^(?:[-*•]|\d+[.)])\s+", line))
            text = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line)
            style = '<w:pStyle w:val="ListBullet"/>' if bullet else ''
            paragraphs.append(f'<w:p><w:pPr>{style}</w:pPr><w:r><w:t>{esc_xml(text)}</w:t></w:r></w:p>')
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{"".join(paragraphs)}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    styles = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style></w:styles>'
    content_types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    package_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    document_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", package_rels)
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", styles)
        package.writestr("word/_rels/document.xml.rels", document_rels)
    return buffer.getvalue()


@app.get(f"{settings.api_prefix}/content/assets/{{asset_id}}/export", response_model=None)
def export_content_asset(asset_id: str, format: str = "markdown"):
    asset = next((row for row in list_items("generated_assets") if row.get("id") == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail="Content asset not found")
    normalized = format.lower()
    if normalized not in {"markdown", "json", "txt", "html", "pptx", "docx"}:
        raise HTTPException(status_code=400, detail="Asset export format must be markdown, json, txt, html, pptx, or docx")
    content = asset.get("content") if isinstance(asset.get("content"), dict) else {"output": asset.get("content", "")}
    title = asset.get("title") or "Generated event asset"
    safe_name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "event-asset"
    filename = f"{safe_name}.{normalized if normalized != 'markdown' else 'md'}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if normalized == "json":
        return JSONResponse(content=asset, headers=headers)
    output = str(content.get("output") or content.get("body") or "").strip()
    if normalized == "html":
        event = next((row for row in list_items("events") if row.get("id") == asset.get("event_id")), {})
        brand = get_brand_kit(event.get("organization_id") or "org-demo")
        return PlainTextResponse(_slide_deck_html(title, output, brand.get("primary_color", "#7568f3"), brand.get("accent_color", "#e4ff63")), media_type="text/html", headers=headers)
    if normalized == "pptx":
        event = next((row for row in list_items("events") if row.get("id") == asset.get("event_id")), {})
        brand = get_brand_kit(event.get("organization_id") or "org-demo")
        return Response(content=_pptx_bytes(title, output, brand.get("primary_color", "#7568f3")), media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", headers=headers)
    if normalized == "docx":
        return Response(content=_docx_bytes(title, output), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)
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
    evidence_lines = []
    for session in sessions:
        evidence_lines.append(f"Session: {session.get('title', 'Untitled')} | Track: {session.get('track') or 'Unassigned'}")
        evidence_lines.extend(f"Evidence: {row.get('snippet', '')}" for row in session.get('evidence', []))
    evidence_lines.extend(f"Takeaway: {row.get('title', '')} — {row.get('body', '')}" for row in snapshot.get('takeaways', []) if not allowed or row.get('session_id') in allowed)
    report_source = "\n".join(line for line in evidence_lines if line.strip())[:120000]
    if report_source:
        asset_type = {
            "executive_brief": "executive_brief",
            "full_event_summary": "attendee_recap",
            "sponsor_report": "sponsor_update",
            "industry_trends": "blog_article",
            "speaker_performance": "speaker_pack",
            "engagement_report": "executive_brief",
            "topic_analysis": "executive_brief",
            "attendee_feedback": "attendee_recap",
        }.get(request.report_type, "executive_brief")
        try:
            generated = generate_content_ai(report_source, request.targetLanguage, asset_type)
            content["generated_output"] = generated.get("output", "")
            content["generation_mode"] = "ai"
            content["model"] = generated.get("model", "")
        except Exception:
            content["generated_output"] = "This report is grounded in the selected event evidence. Review the linked themes, sessions, takeaways, and source passages before sharing."
            content["generation_mode"] = "fallback"
            content["model"] = "local-fallback"
    report = create_report({"event_id": request.event_id, "source_session_ids": [row.get("session_id") for row in sessions], "report_type": request.report_type, "title": request.title, "format": request.format, "content": content})
    return {"mode": content.get("generation_mode", snapshot.get("mode", "grounded-local")), "model": content.get("model"), "report": report}


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
    if content.get("generated_output"): lines.extend(["## AI-generated report", str(content["generated_output"]), ""])
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


@app.post(f"{settings.api_prefix}/content/assets/{{asset_id}}/publish")
def publish_generated_asset(asset_id: str) -> dict:
    asset = next((row for row in list_items("generated_assets") if row.get("id") == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail="Content asset not found")
    if not str(asset.get("content", {}).get("output", "") if isinstance(asset.get("content"), dict) else asset.get("content", "")).strip():
        raise HTTPException(status_code=400, detail="Add content before publishing this asset")
    try:
        return update_generated_asset(asset_id, {"status": "published"})
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
def get_takeaways(event_id: str | None = None, session_id: str | None = None, share_token: str | None = None, authorization: str | None = Header(default=None)) -> list[dict]:
    _require_portal_access(authorization, share_token, session_id=session_id, event_id=event_id)
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
    evidence_rows = [row for row in (list_items("transcripts") + list_items("insights")) if (not request.event_id or row.get("event_id") == request.event_id) and (not request.session_id or row.get("session_id") == request.session_id)]
    evidence = [{"id": row.get("id"), "session_id": row.get("session_id"), "speaker": row.get("speaker"), "timestamp": row.get("created_at"), "snippet": (row.get("text") or row.get("body") or row.get("title") or "")[:240]} for row in evidence_rows[:20]]
    try:
        result = generate_content_ai(request.text, request.targetLanguage, request.asset_type)
        mode = "ai"
    except Exception:
        fallback_titles = {"executive_brief": "Executive brief", "attendee_recap": "Attendee recap", "speaker_pack": "Speaker pack", "social_carousel": "Social carousel", "followup_email": "Follow-up email", "sponsor_update": "Sponsor update", "linkedin_post": "LinkedIn post", "quote_card": "Quote card", "blog_article": "Event article", "newsletter": "Event newsletter", "event_microsite": "Event microsite outline", "presentation_outline": "Presentation outline"}
        result = {"model": "local-fallback", "targetLanguage": request.targetLanguage, "assetType": request.asset_type, "output": f"{fallback_titles.get(request.asset_type, 'Event content')}\n\nKey signal\n{request.text[:420]}\n\nNext action\nShare this evidence with the event team and approve the final version before publishing."}
        mode = "fallback"
    asset = create_generated_asset({"event_id": request.event_id, "session_id": request.session_id, "asset_type": request.asset_type, "title": request.title, "content": {"output": result.get("output", ""), "targetLanguage": request.targetLanguage, "model": result.get("model", ""), "evidence": evidence}, "status": "draft"})
    return {**result, "asset_type": request.asset_type, "mode": mode, "evidence": evidence, "asset": asset}


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
    """Accept authenticated live text or binary audio chunks from a venue bridge."""
    await websocket.accept()
    if not await _websocket_authenticated(websocket):
        await websocket.close(code=1008, reason="Organizer authentication is required")
        return
    try:
        while True:
            frame = await websocket.receive()
            if frame.get("type") == "websocket.disconnect":
                return
            audio = frame.get("bytes")
            payload: dict[str, Any] = {}
            if audio:
                try:
                    result = transcribe(audio, "audio/webm", [])
                    payload = {"text": result.get("transcript", ""), "model": result.get("model", "live-audio"), "language": result.get("language", "auto")}
                except Exception as exc:
                    logger.exception("Live audio transcription failed: %s", exc)
                    await websocket.send_json({"type": "error", "detail": "Live audio transcription failed. Check the provider configuration and retry."})
                    continue
            else:
                try:
                    payload = json.loads(frame.get("text") or "{}")
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "Live capture frames must be JSON text or audio bytes"})
                    continue
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
def create_translation(request: TranslateRequest, share_token: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    _require_portal_access(authorization, share_token, session_id=request.session_id)
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Transcript text is required")
    try:
        return translate(request.text, request.targetLanguage)
    except Exception:
        return {"model": "local-fallback", "targetLanguage": request.targetLanguage, "mode": "fallback", "output": f"[{request.targetLanguage}] {request.text.strip()}"}


@app.post(f"{settings.api_prefix}/transcription/batch")
async def transcribe_audio(file: UploadFile = File(...), session_id: str = Form("ses-001"), language: str = Form("auto"), vocabulary: str = Form("")) -> dict:
    try:
        terms = [item.strip() for item in re.split(r"[,\n]", vocabulary) if item.strip()][:80]
        result = transcribe(await file.read(), file.content_type or "audio/webm", [] if language == "auto" else [language], terms)
        capture = {"text": result["transcript"], "model": result["model"], "language": language, "session_id": session_id, "speaker": "Detected speaker"}
        saved = create_transcript(capture)
        insight = create_insight(capture)
        return {**result, "transcript": saved, "insight": insight}
    except Exception as exc:
        logger.exception("Batch transcription failed: %s", exc)
        raise HTTPException(status_code=503, detail="Transcription is temporarily unavailable. Check the server AI configuration and retry.") from exc


@app.exception_handler(Exception)
async def unhandled_error(_, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": "The server could not complete that request"})


@app.get("/microsite/{slug}", include_in_schema=False)
def public_microsite(slug: str) -> PlainTextResponse:
    """Render a public, branded event recap page from published event data.

    The page intentionally reads only public event metadata and published content;
    organizer credentials, drafts, private files, and raw transcripts never leave
    the protected workspace through this route.
    """
    normalized = slug.strip().lower()
    event = next((item for item in list_items("events") if str(item.get("slug", "")).lower() == normalized), None)
    if not event or str(event.get("privacy", "public")).lower() == "private":
        return PlainTextResponse("Event microsite not found", status_code=404)
    event_id = event.get("id")
    sessions = [item for item in list_items("sessions") if item.get("event_id") == event_id and item.get("status") != "archived"]
    session_ids = {item.get("id") for item in sessions}
    speakers = [item for item in list_items("speakers") if any(link.get("speaker_id") == item.get("id") and link.get("session_id") in session_ids for link in list_items("session_speakers"))]
    takeaways = [item for item in list_items("takeaways") if item.get("event_id") == event_id or item.get("session_id") in session_ids][:8]
    topics = topic_cloud(event_id)[:12]
    assets = [item for item in list_items("generated_assets") if item.get("event_id") == event_id and item.get("status") == "published"][:8]
    share = next((item for item in list_items("share_links") if item.get("event_id") == event_id), None)
    attendee_url = f"/attendee/{html_escape(str(event.get('slug') or 'event'))}"
    brand = get_brand_kit(event.get("organization_id") or "org-demo") or {}
    primary = html_escape(str(brand.get("primary_color") or event.get("brand_color") or "#7568f3"))
    accent = html_escape(str(brand.get("accent_color") or "#e4ff63"))
    ink = html_escape(str(brand.get("secondary_color") or "#171827"))
    title = html_escape(str(event.get("name") or event.get("title") or "Event recap"))
    description = html_escape(str(event.get("description") or "A public recap of the ideas, people, and moments that shaped this event."))
    venue = html_escape(str(event.get("venue") or "Live event experience"))
    date_label = html_escape(str(event.get("starts_at") or "").split("T")[0])
    session_cards = "".join(f"<article class='session'><span>{html_escape(str(item.get('track') or 'Program'))} · {html_escape(str(item.get('room') or 'Main room'))}</span><h3>{html_escape(str(item.get('title') or 'Session'))}</h3><p>{html_escape(str(item.get('summary') or item.get('description') or 'Session details will be shared here.'))}</p><small>{html_escape(str(item.get('speaker') or 'Event speaker'))}</small></article>" for item in sessions)
    speaker_cards = "".join(f"<article class='speaker'><div class='avatar'>{html_escape(str(item.get('name') or 'S')[:1].upper())}</div><div><strong>{html_escape(str(item.get('name') or 'Speaker'))}</strong><span>{html_escape(' · '.join(filter(None, [str(item.get('title') or ''), str(item.get('company') or '')])))} </span><p>{html_escape(str(item.get('biography') or ''))}</p></div></article>" for item in speakers)
    takeaway_cards = "".join(f"<article class='takeaway'><b>{html_escape(str(item.get('title') or 'Event signal'))}</b><p>{html_escape(str(item.get('body') or ''))}</p></article>" for item in takeaways)
    topic_pills = "".join(f"<span>{html_escape(str(item.get('label') or 'Topic'))}<small>{html_escape(str(item.get('count') or 0))}</small></span>" for item in topics)
    asset_cards = "".join(f"<article class='asset'><span>Published content</span><h3>{html_escape(str(item.get('title') or 'Event asset'))}</h3><p>{html_escape(str((item.get('content') or {}).get('output', '') if isinstance(item.get('content'), dict) else item.get('content', ''))[:420])}</p></article>" for item in assets)
    token_suffix = f"?share={html_escape(str(share.get('token')))}" if share and share.get("token") else ""
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} · Event recap</title><meta name='description' content='{description}'><style>
    :root{{--primary:{primary};--accent:{accent};--ink:{ink};--paper:#f6f5f1;--muted:#6d7181}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit}}.wrap{{max-width:1160px;margin:auto;padding:0 28px}}nav{{display:flex;justify-content:space-between;align-items:center;padding:24px 0}}.brand{{font-weight:900;letter-spacing:-.04em;font-size:20px}}.brand i{{display:inline-block;width:14px;height:14px;border-radius:5px;background:var(--primary);margin-right:8px}}.navlinks{{display:flex;gap:18px;align-items:center;font-size:13px;font-weight:800}}.btn{{display:inline-flex;text-decoration:none;border-radius:999px;padding:12px 18px;font-weight:850;background:var(--ink);color:white}}.btn.accent{{background:var(--accent);color:var(--ink)}}.hero{{padding:72px 0 90px;display:grid;grid-template-columns:1.2fr .8fr;gap:46px;align-items:end}}.eyebrow{{color:var(--primary);text-transform:uppercase;letter-spacing:.14em;font-weight:900;font-size:12px}}h1{{font-size:clamp(48px,8vw,92px);line-height:.95;letter-spacing:-.08em;margin:14px 0 24px;max-width:780px}}h2{{font-size:clamp(30px,5vw,54px);line-height:1;letter-spacing:-.06em;margin:8px 0 16px}}h3{{line-height:1.1;letter-spacing:-.03em;margin:10px 0}}.hero p{{font-size:20px;color:var(--muted);max-width:650px}}.hero-card{{background:var(--ink);color:#fff;border-radius:28px;padding:28px;min-height:250px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:16px 16px 0 var(--primary)}}.hero-card strong{{font-size:30px;line-height:1.05;letter-spacing:-.05em}}.section{{padding:70px 0;border-top:1px solid #deddd7}}.section-head{{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:28px}}.section-head p{{color:var(--muted);max-width:430px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.session,.takeaway,.asset,.speaker{{background:white;border:1px solid #e2e1dc;border-radius:18px;padding:22px;box-shadow:0 8px 24px #1718270c}}.session span,.asset span,.speaker span{{color:var(--muted);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}.session p,.takeaway p,.asset p,.speaker p{{color:var(--muted);margin:8px 0}}.speaker{{display:flex;gap:14px}}.avatar{{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:var(--primary);color:white;font-weight:900;flex:0 0 auto}}.topics{{display:flex;flex-wrap:wrap;gap:10px}}.topics span{{padding:10px 14px;border-radius:999px;background:#fff;border:1px solid #dfded8;font-weight:850}}.topics small{{color:var(--primary);margin-left:6px}}.cta{{background:var(--primary);color:white;border-radius:26px;padding:42px;display:flex;justify-content:space-between;gap:20px;align-items:center}}footer{{padding:34px 0 50px;color:var(--muted);font-size:13px}}@media(max-width:800px){{.hero{{grid-template-columns:1fr;padding:44px 0 64px}}.grid{{grid-template-columns:1fr}}.section-head,.cta{{display:block}}.section-head p{{margin-top:18px}}.navlinks a:not(.btn){{display:none}}}}
    </style></head><body><div class='wrap'><nav><a class='brand' href='/'><i></i>smart event manager</a><div class='navlinks'><a href='#program'>Program</a><a href='#signals'>Signals</a><a class='btn accent' href='{attendee_url}{token_suffix}'>Join the event ↗</a></div></nav><main><section class='hero'><div><div class='eyebrow'>{venue} · {date_label}</div><h1>{title}</h1><p>{description}</p><a class='btn accent' href='{attendee_url}{token_suffix}'>Open attendee experience ↗</a></div><div class='hero-card'><span>Event content intelligence</span><strong>Ideas from the room, made useful everywhere.</strong><span>{len(sessions)} sessions · {len(topics)} topic signals · {len(assets)} published assets</span></div></section><section class='section' id='program'><div class='section-head'><div><div class='eyebrow'>The program</div><h2>What happened in the room.</h2></div><p>Explore the sessions and people behind the event conversation.</p></div><div class='grid'>{session_cards or '<article class="session"><h3>Program coming soon</h3><p>Session details will appear after the organizer publishes the event.</p></article>'}</div></section><section class='section' id='signals'><div class='section-head'><div><div class='eyebrow'>Event signals</div><h2>The ideas worth carrying forward.</h2></div><p>These takeaways and topics are drawn from the event knowledge layer.</p></div><div class='grid'>{takeaway_cards or '<article class="takeaway"><b>Signals will appear after capture.</b><p>The organizer is still building the event recap.</p></article>'}</div><div class='topics' style='margin-top:18px'>{topic_pills or '<span>Event topics coming soon</span>'}</div></section><section class='section'><div class='section-head'><div><div class='eyebrow'>People</div><h2>Voices shaping the conversation.</h2></div></div><div class='grid'>{speaker_cards or '<article class="speaker"><div><strong>Speakers coming soon</strong><p>Speaker profiles will be published with the program.</p></div></article>'}</div></section><section class='section'><div class='section-head'><div><div class='eyebrow'>Published content</div><h2>Keep exploring after the event.</h2></div></div><div class='grid'>{asset_cards or '<article class="asset"><span>Content studio</span><h3>More recaps are on the way.</h3><p>The event team will publish approved briefs, recaps, and speaker packs here.</p></article>'}</div></section><section class='section'><div class='cta'><div><div class='eyebrow' style='color:var(--accent)'>Continue the experience</div><h2>Stay close to the signal.</h2><p>Open the attendee portal for live captions, questions, takeaways, and translations.</p></div><a class='btn accent' href='{attendee_url}{token_suffix}'>Join the portal ↗</a></div></section></main><footer>Published with Smart Event Manager · {title}</footer></div></body></html>"""
    return PlainTextResponse(html, media_type="text/html")


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
