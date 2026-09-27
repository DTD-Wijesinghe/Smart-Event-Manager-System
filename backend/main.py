from pathlib import Path
from typing import Any
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import FRONTEND_DIR, settings
from .repository import analytics, create_event, create_feedback, create_generated_asset, create_insight, create_poll, create_question, create_session, create_transcript, dashboard, delete_event, delete_session, duplicate_session, get_share_link, list_items, respond_poll, search_knowledge, set_session_status, storage_mode, topic_cloud, update_event, update_session, vote_question
from .vertex_ai import analyst_answer, summarize, transcribe, translate


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


class AuthRequest(BaseModel):
    email: str
    password: str


class RecoveryRequest(BaseModel):
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ResetPasswordRequest(BaseModel):
    password: str


class AnalystRequest(BaseModel):
    question: str
    event_id: str | None = None
    session_id: str | None = None


app = FastAPI(title="Smart Event Manager API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=settings.allowed_origins != ["*"], allow_methods=["*"], allow_headers=["*"])


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


@app.post(f"{settings.api_prefix}/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
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
        return {"mode": "demo", "session": {"access_token": "demo-session", "refresh_token": request.refresh_token}}
    response = httpx.post(f"{settings.supabase_url}/auth/v1/token?grant_type=refresh_token", headers=_auth_headers(), json={"refresh_token": request.refresh_token}, timeout=20)
    if not response.is_success:
        raise HTTPException(status_code=response.status_code, detail="Session refresh failed")
    return {"mode": "supabase", "session": response.json()}


@app.post(f"{settings.api_prefix}/auth/reset-password")
def reset_password(request: ResetPasswordRequest, authorization: str | None = Header(default=None)) -> dict:
    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not settings.supabase_url or not settings.supabase_anon_key:
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
def get_dashboard() -> dict: return dashboard()


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
def stop_session(session_id: str) -> dict:
    try: return set_session_status(session_id, "completed")
    except (KeyError, ValueError) as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/events")
def get_events() -> list[dict]: return list_items("events")


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
def get_transcripts(session_id: str | None = None) -> list[dict]:
    rows = list_items("transcripts")
    return [row for row in rows if not session_id or row.get("session_id") == session_id]


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
    options = list_items("poll_options")
    return [{**item, "options": sorted([option for option in options if option.get("poll_id") == item.get("id")], key=lambda option: option.get("sort_order", 0))} for item in polls if not session_id or item.get("session_id") == session_id]


@app.post(f"{settings.api_prefix}/polls", status_code=201)
def add_poll(request: PollRequest) -> dict:
    if len(request.question.strip()) < 3: raise HTTPException(status_code=400, detail="Poll question is too short")
    if request.poll_type in {"single", "multiple", "yes_no"} and len(request.options) < 2: raise HTTPException(status_code=400, detail="At least two poll options are required")
    return create_poll(request.model_dump())


@app.post(f"{settings.api_prefix}/poll-responses", status_code=201)
def add_poll_response(request: PollResponseRequest) -> dict:
    poll = next((item for item in list_items("polls") if item.get("id") == request.poll_id), None)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
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
    sources = search_knowledge(request.question, request.event_id)
    if request.session_id:
        sources = [source for source in sources if source.get("session_id") == request.session_id]
    if not sources:
        return {"mode": "grounded", "answer": "I could not find supporting event content for that question yet.", "citations": []}
    try:
        return {"mode": "ai", **analyst_answer(request.question, sources)}
    except Exception:
        excerpts = " ".join(source["snippet"] for source in sources[:3])
        return {"mode": "fallback", "answer": f"Relevant event evidence: {excerpts}", "citations": sources[:3]}


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


@app.websocket(f"{settings.api_prefix}/ws/capture/{{session_id}}")
async def capture_socket(websocket: WebSocket, session_id: str) -> None:
    """Accept live text chunks from a venue bridge or meeting integration."""
    await websocket.accept()
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
async def unhandled_error(_, exc: Exception) -> JSONResponse: return JSONResponse(status_code=500, content={"error": str(exc)})


app.mount("/", StaticFiles(directory=Path(FRONTEND_DIR), html=True), name="frontend")
