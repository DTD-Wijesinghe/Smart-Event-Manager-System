from functools import lru_cache
import json
from google import genai
from google.genai import types
from google.oauth2 import service_account
from .config import settings


@lru_cache(maxsize=1)
def client() -> genai.Client:
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key)
    if not settings.project or not (settings.credentials_path or settings.credentials_json):
        raise RuntimeError("GCP_PROJECT and VERTEX_SERVICE_ACCOUNT_JSON are required")
    if settings.credentials_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(settings.credentials_json), scopes=["https://www.googleapis.com/auth/cloud-platform"])
    else:
        credentials = service_account.Credentials.from_service_account_file(settings.credentials_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return genai.Client(vertexai=True, project=settings.project, location=settings.location, credentials=credentials, http_options=types.HttpOptions(api_version="v1"))


def summarize(text: str, target_language: str = "English") -> dict:
    prompt = f"You are the content intelligence layer for Smart Event Manager. Summarize this event transcript for an organizer in {target_language}. Return sections: Summary, Key signals, Action items, Social post. Do not invent facts.\n\nTranscript:\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "output": response.text or ""}


def generate_content(text: str, target_language: str = "English", asset_type: str = "attendee_recap") -> dict:
    instructions = {
        "executive_brief": "Create a concise executive brief with Summary, decisions, risks, opportunities, and recommended next actions.",
        "attendee_recap": "Create a warm attendee recap with the strongest ideas, practical takeaways, notable quotes, and what to explore next.",
        "speaker_pack": "Create a speaker pack with a session summary, key quotes, audience questions, and three social-ready moments.",
        "social_carousel": "Create an 8-slide social carousel. For every slide return a short headline and 1-2 sentence caption, followed by a final post caption and hashtags.",
        "followup_email": "Create a polished post-event follow-up email with subject line, concise recap, three takeaways, and a clear next step.",
        "sponsor_update": "Create a sponsor-ready impact update with audience signals, themes, proof points, and suggested follow-up language.",
    }
    instruction = instructions.get(asset_type, instructions["attendee_recap"])
    prompt = f"You are Smart Event Manager's content studio. {instruction} Write in {target_language}. Use only the supplied event evidence; do not invent facts.\n\nEvent evidence:\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "assetType": asset_type, "output": response.text or ""}


def rewrite_content(text: str, instruction: str, target_language: str = "English", asset_type: str = "attendee_recap") -> dict:
    prompt = f"You are editing a Smart Event Manager event asset. Rewrite the supplied asset according to the editor instruction. Keep every factual claim grounded in the asset; do not add facts, names, quotes, or numbers. Preserve the useful structure and write in {target_language}. Asset type: {asset_type}. Editor instruction: {instruction}\n\nCurrent asset:\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "assetType": asset_type, "output": response.text or ""}


def translate(text: str, target_language: str = "English") -> dict:
    prompt = f"Translate the following event transcript into {target_language}. Preserve the meaning, speaker tone, and paragraph breaks. Return only the translation.\n\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "output": response.text or ""}


def analyst_answer(question: str, sources: list[dict]) -> dict:
    context = "\n\n".join(f"[{index + 1}] Session: {source.get('session_title') or source.get('session_id')} | Speaker: {source.get('speaker') or 'Unknown'} | Source: {source.get('snippet', '')}" for index, source in enumerate(sources[:12]))
    prompt = f"Answer the event analyst question using only the supplied source excerpts. If the sources are insufficient, say so. Cite supporting excerpts with [1], [2] markers. Question: {question}\n\nSources:\n{context}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "answer": response.text or "", "citations": sources[:12]}


def transcribe(audio: bytes, mime_type: str = "audio/webm", language_codes: list[str] | None = None) -> dict:
    if len(audio) > 20 * 1024 * 1024:
        raise ValueError("Inline audio is limited to 20 MB")
    contents = types.Content(parts=[types.Part.from_bytes(data=audio, mime_type=mime_type)])
    response = client().models.generate_content(model=settings.batch_model, contents=contents, config=types.GenerateContentConfig(system_instruction="Transcribe the provided event audio accurately. Identify speakers when possible and preserve the spoken language."))
    return {"model": settings.batch_model, "transcript": response.text or "", "languageCodes": language_codes or []}
