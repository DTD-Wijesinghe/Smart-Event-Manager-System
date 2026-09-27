"""Durable media storage for uploaded event recordings.

Render's filesystem is ephemeral, so deployed uploads must live in Supabase
Storage. The local filesystem remains available for the intentionally local
demo mode.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import settings


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_storage_key)


def _headers(content_type: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": settings.supabase_storage_key,
        "Authorization": f"Bearer {settings.supabase_storage_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def ensure_bucket() -> None:
    if not configured():
        raise RuntimeError("Supabase Storage is not configured")
    response = httpx.post(
        f"{settings.supabase_url}/storage/v1/bucket",
        headers={**_headers("application/json"), "Accept": "application/json"},
        json={"id": settings.supabase_storage_bucket, "name": settings.supabase_storage_bucket, "public": False},
        timeout=20,
    )
    # Supabase returns a conflict when the bucket already exists.
    if response.status_code not in {200, 201, 409}:
        response.raise_for_status()


def upload_bytes(path: str, content: bytes, content_type: str) -> str:
    ensure_bucket()
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    response = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/{quote(settings.supabase_storage_bucket, safe='')}/{encoded_path}",
        headers={**_headers(content_type), "x-upsert": "true"},
        content=content,
        timeout=60,
    )
    response.raise_for_status()
    return path


def download_bytes(path: str) -> bytes:
    """Read a private upload back from Supabase Storage for a retry job."""
    if not configured():
        raise RuntimeError("Supabase Storage is not configured")
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    response = httpx.get(
        f"{settings.supabase_url}/storage/v1/object/{quote(settings.supabase_storage_bucket, safe='')}/{encoded_path}",
        headers=_headers(),
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def delete_object(path: str) -> None:
    if not configured() or not path:
        return
    response = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/remove",
        headers={**_headers("application/json"), "Accept": "application/json"},
        json={"prefixes": [path]},
        timeout=20,
    )
    if response.status_code not in {200, 204, 404}:
        response.raise_for_status()


def signed_url(path: str, expires_in: int = 3600) -> str:
    """Return a short-lived URL for a private object without exposing keys."""
    if not configured():
        raise RuntimeError("Supabase Storage is not configured")
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    response = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/sign/{quote(settings.supabase_storage_bucket, safe='')}/{encoded_path}",
        headers={**_headers("application/json"), "Accept": "application/json"},
        json={"expiresIn": max(60, min(int(expires_in), 86400))},
        timeout=20,
    )
    response.raise_for_status()
    value = response.json().get("signedURL") or response.json().get("signedUrl")
    if not value:
        raise RuntimeError("Supabase did not return a signed URL")
    return value if value.startswith("http") else f"{settings.supabase_url}/storage/v1{value}"
