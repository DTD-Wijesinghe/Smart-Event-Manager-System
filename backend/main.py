from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import FRONTEND_DIR, settings
from .repository import create_session, create_transcript, dashboard, list_items, storage_mode
from .vertex_ai import summarize, transcribe


class SummaryRequest(BaseModel):
    text: str
    targetLanguage: str = "English"


app = FastAPI(title="Smart Event Manager API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=settings.allowed_origins != ["*"], allow_methods=["*"], allow_headers=["*"])


@app.get(f"{settings.api_prefix}/health")
def health() -> dict[str, Any]:
    return {"ok": True, "mode": storage_mode(), "vertexConfigured": bool(settings.project and settings.credentials_path), "project": settings.project or None}


@app.get(f"{settings.api_prefix}/dashboard")
def get_dashboard() -> dict: return dashboard()


@app.get(f"{settings.api_prefix}/sessions")
def get_sessions() -> list[dict]: return list_items("sessions")


@app.post(f"{settings.api_prefix}/sessions", status_code=201)
def add_session(payload: dict) -> dict: return create_session(payload)


@app.get(f"{settings.api_prefix}/attendees")
def get_attendees() -> list[dict]: return list_items("attendees")


@app.get(f"{settings.api_prefix}/insights")
def get_insights() -> list[dict]: return list_items("insights")


@app.get(f"{settings.api_prefix}/share-links")
def get_share_links() -> list[dict]: return list_items("share_links")


@app.get(f"{settings.api_prefix}/transcripts")
def get_transcripts() -> list[dict]: return list_items("transcripts")


@app.post(f"{settings.api_prefix}/ai/summarize")
def create_summary(request: SummaryRequest) -> dict:
    if not request.text.strip(): raise HTTPException(status_code=400, detail="Transcript text is required")
    try: return summarize(request.text, request.targetLanguage)
    except Exception as exc: raise HTTPException(status_code=503, detail=str(exc)) from exc


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
