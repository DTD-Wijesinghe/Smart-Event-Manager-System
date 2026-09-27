from pathlib import Path
from typing import Any
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import FRONTEND_DIR, settings
from .repository import analytics, create_event, create_feedback, create_generated_asset, create_insight, create_poll, create_question, create_session, create_transcript, dashboard, get_share_link, list_items, respond_poll, storage_mode, update_event, vote_question
from .vertex_ai import summarize, transcribe, translate


class SummaryRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


class TranslateRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


class CaptureRequest(BaseModel):
    text: str
    session_id: str = "ses-001"
    language: str = "auto"
    speaker: str = "Live speaker"


class QuestionRequest(BaseModel):
    body: str
    session_id: str = "ses-001"
    anonymous: bool = False


class VoteRequest(BaseModel):
    voter_id: str = "anonymous"


class PollRequest(BaseModel):
    question: str
    session_id: str = "ses-001"
    poll_type: str = "single"
    options: list[str] = []


class PollResponseRequest(BaseModel):
    poll_id: str
    option_id: str | None = None
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


class AuthRequest(BaseModel):
    email: str
    password: str


class RecoveryRequest(BaseModel):
    email: str


app = FastAPI(title="Smart Event Manager API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=settings.allowed_origins != ["*"], allow_methods=["*"], allow_headers=["*"])


@app.get(f"{settings.api_prefix}/health")
def health() -> dict[str, Any]:
    return {"ok": True, "mode": storage_mode(), "vertexConfigured": bool(settings.project and settings.credentials_path and Path(settings.credentials_path).exists()), "project": settings.project or None}


def _auth_headers() -> dict[str, str]:
    return {"apikey": settings.supabase_anon_key, "Content-Type": "application/json"}


@app.post(f"{settings.api_prefix}/auth/register", status_code=201)
def register(request: AuthRequest) -> dict:
    if len(request.password) < 8: raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not settings.supabase_url or not settings.supabase_anon_key:
        return {"mode": "demo", "session": {"access_token": "demo-session", "user": {"email": request.email}}}
    response = httpx.post(f"{settings.supabase_url}/auth/v1/signup", headers=_auth_headers(), json={"email": request.email, "password": request.password}, timeout=20)
    if not response.is_success:
        detail = response.json().get("msg") or response.json().get("error_description") or "Registration failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return {"mode": "supabase", "session": response.json()}


@app.post(f"{settings.api_prefix}/auth/login")
def login(request: AuthRequest) -> dict:
    if not settings.supabase_url or not settings.supabase_anon_key:
        return {"mode": "demo", "session": {"access_token": "demo-session", "user": {"email": request.email}}}
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


@app.get(f"{settings.api_prefix}/dashboard")
def get_dashboard() -> dict: return dashboard()


@app.get(f"{settings.api_prefix}/sessions")
def get_sessions() -> list[dict]: return list_items("sessions")


@app.post(f"{settings.api_prefix}/sessions", status_code=201)
def add_session(payload: dict) -> dict: return create_session(payload)


@app.get(f"{settings.api_prefix}/events")
def get_events() -> list[dict]: return list_items("events")


@app.post(f"{settings.api_prefix}/events", status_code=201)
def add_event(payload: dict) -> dict: return create_event(payload)


@app.patch(f"{settings.api_prefix}/events/{{event_id}}")
def edit_event(event_id: str, payload: dict) -> dict:
    try: return update_event(event_id, payload)
    except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/attendees")
def get_attendees() -> list[dict]: return list_items("attendees")


@app.get(f"{settings.api_prefix}/insights")
def get_insights() -> list[dict]: return list_items("insights")


@app.get(f"{settings.api_prefix}/share-links")
def get_share_links() -> list[dict]: return list_items("share_links")


@app.get(f"{settings.api_prefix}/public/share/{{token}}")
def get_public_share(token: str) -> dict:
    result = get_share_link(token)
    if not result:
        raise HTTPException(status_code=404, detail="Share link not found")
    return result


@app.get(f"{settings.api_prefix}/transcripts")
def get_transcripts() -> list[dict]: return list_items("transcripts")


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


@app.get(f"{settings.api_prefix}/polls")
def get_polls(session_id: str | None = None) -> list[dict]:
    polls = list_items("polls")
    return [item for item in polls if not session_id or item.get("session_id") == session_id]


@app.post(f"{settings.api_prefix}/polls", status_code=201)
def add_poll(request: PollRequest) -> dict:
    if len(request.question.strip()) < 3: raise HTTPException(status_code=400, detail="Poll question is too short")
    if request.poll_type in {"single", "multiple", "yes_no"} and len(request.options) < 2: raise HTTPException(status_code=400, detail="At least two poll options are required")
    return create_poll(request.model_dump())


@app.post(f"{settings.api_prefix}/poll-responses", status_code=201)
def add_poll_response(request: PollResponseRequest) -> dict: return respond_poll(request.model_dump())


@app.post(f"{settings.api_prefix}/feedback", status_code=201)
def add_feedback(request: FeedbackRequest) -> dict:
    for rating in (request.speaker_rating, request.content_rating, request.session_rating):
        if rating is not None and not 1 <= rating <= 5: raise HTTPException(status_code=400, detail="Ratings must be between 1 and 5")
    return create_feedback(request.model_dump())


@app.get(f"{settings.api_prefix}/analytics")
def get_analytics(event_id: str = "evt-001") -> dict: return analytics(event_id)


@app.get(f"{settings.api_prefix}/content/assets")
def get_generated_assets() -> list[dict]: return list_items("generated_assets")


@app.post(f"{settings.api_prefix}/content/generate", status_code=201)
def generate_content(request: ContentGenerateRequest) -> dict:
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Source text is required")
    result = create_summary(SummaryRequest(text=request.text, targetLanguage=request.targetLanguage))
    asset = create_generated_asset({"event_id": request.event_id, "session_id": request.session_id, "asset_type": request.asset_type, "title": request.title, "content": {"output": result.get("output", ""), "targetLanguage": request.targetLanguage, "model": result.get("model", "")}, "status": "draft"})
    return {**result, "asset": asset}


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
        saved = create_transcript({"text": result["transcript"], "model": result["model"], "language": language, "session_id": session_id})
        return {**result, "transcript": saved}
    except Exception as exc: raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled_error(_, exc: Exception) -> JSONResponse: return JSONResponse(status_code=500, content={"error": str(exc)})


app.mount("/", StaticFiles(directory=Path(FRONTEND_DIR), html=True), name="frontend")
