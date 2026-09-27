from functools import lru_cache
from google import genai
from google.genai import types
from google.oauth2 import service_account
from .config import settings


@lru_cache(maxsize=1)
def client() -> genai.Client:
    if not settings.project or not settings.credentials_path:
        raise RuntimeError("GCP_PROJECT and VERTEX_SERVICE_ACCOUNT_JSON are required")
    credentials = service_account.Credentials.from_service_account_file(settings.credentials_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return genai.Client(vertexai=True, project=settings.project, location=settings.location, credentials=credentials, http_options=types.HttpOptions(api_version="v1"))


def summarize(text: str, target_language: str = "English") -> dict:
    prompt = f"You are the content intelligence layer for Smart Event Manager. Summarize this event transcript for an organizer in {target_language}. Return sections: Summary, Key signals, Action items, Social post. Do not invent facts.\n\nTranscript:\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "output": response.text or ""}


def translate(text: str, target_language: str = "English") -> dict:
    prompt = f"Translate the following event transcript into {target_language}. Preserve the meaning, speaker tone, and paragraph breaks. Return only the translation.\n\n{text[:120000]}"
    response = client().models.generate_content(model=settings.text_model, contents=prompt)
    return {"model": settings.text_model, "targetLanguage": target_language, "output": response.text or ""}


def transcribe(audio: bytes, mime_type: str = "audio/webm", language_codes: list[str] | None = None) -> dict:
    if len(audio) > 20 * 1024 * 1024:
        raise ValueError("Inline audio is limited to 20 MB")
    contents = types.Content(parts=[types.Part.from_bytes(data=audio, mime_type=mime_type)])
    response = client().models.generate_content(model=settings.batch_model, contents=contents, config=types.GenerateContentConfig(system_instruction="Transcribe the provided event audio accurately. Identify speakers when possible and preserve the spoken language."))
    return {"model": settings.batch_model, "transcript": response.text or "", "languageCodes": language_codes or []}
