from pathlib import Path
import os
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def origins(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] or ["*"]


class Settings:
    app_host = os.getenv("APP_HOST", "0.0.0.0")
    app_port = int(os.getenv("APP_PORT", "8000"))
    api_prefix = os.getenv("API_PREFIX", "/api")
    allowed_origins = origins(os.getenv("ALLOWED_ORIGINS", "*"))
    project = os.getenv("GCP_PROJECT", "")
    location = os.getenv("GCP_LOCATION", "global")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    _credentials_value = os.getenv("VERTEX_SERVICE_ACCOUNT_JSON", "")
    credentials_json = _credentials_value if _credentials_value.lstrip().startswith("{") else ""
    credentials_path = str((ROOT_DIR / _credentials_value).resolve()) if _credentials_value and not credentials_json and not Path(_credentials_value).is_absolute() else (_credentials_value if not credentials_json else "")
    text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    batch_model = os.getenv("GEMINI_BATCH_MODEL", "gemini-3.5-transcribe")
    live_model = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.5-transcribe-live")
    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
    max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "20"))
    demo_session_secret = os.getenv("DEMO_SESSION_SECRET", "smart-event-manager-demo-session-v1")


settings = Settings()
FRONTEND_DIR = ROOT_DIR / "frontend"
