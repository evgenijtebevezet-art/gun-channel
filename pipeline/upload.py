"""
upload.py — загрузка на YouTube + хранение в Google Drive
"""

import os
import json
import logging
import time
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)


# ─── Google Drive ─────────────────────────────────────────────────────────────

def upload_to_drive(file_path: str, filename: str = None, folder_id: str = None) -> str | None:
    """Загружает файл на Google Drive. Возвращает file_id."""
    token = _get_google_token()
    if not token:
        log.error("Нет Google токена для Drive")
        return None

    filename = filename or Path(file_path).name
    folder_id = folder_id or config.GDRIVE_FOLDER_ID

    try:
        # Metadata
        metadata = {"name": filename}
        if folder_id:
            metadata["parents"] = [folder_id]

        file_size = os.path.getsize(file_path)
        mime = "video/mp4" if file_path.endswith(".mp4") else "application/octet-stream"

        # Resumable upload для файлов > 5MB
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": mime,
            "X-Upload-Content-Length": str(file_size),
        }

        init_resp = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
            headers=headers,
            json=metadata,
            timeout=30,
        )
        init_resp.raise_for_status()
        upload_url = init_resp.headers["Location"]

        # Загружаем файл чанками
        chunk_size = 5 * 1024 * 1024  # 5MB
        uploaded = 0

        with open(file_path, "rb") as f:
            while uploaded < file_size:
                chunk = f.read(chunk_size)
                end = uploaded + len(chunk) - 1

                chunk_headers = {
                    "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
                    "Content-Type": mime,
                }
                resp = requests.put(upload_url, headers=chunk_headers, data=chunk, timeout=120)

                if resp.status_code in (200, 201):
                    file_id = resp.json().get("id")
                    log.info(f"Drive upload OK: {filename} → {file_id}")
                    return file_id
                elif resp.status_code == 308:  # продолжаем
                    uploaded += len(chunk)
                else:
                    log.error(f"Drive chunk error: {resp.status_code}")
                    return None

    except Exception as e:
        log.error(f"Drive upload error: {e}")
        return None

    return None


# ─── YouTube Upload ───────────────────────────────────────────────────────────

def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] = None,
    category_id: str = "19",  # 19 = Travel & Events, 17 = Sports, 28 = Science & Technology
) -> str | None:
    """
    Загружает видео на YouTube.
    Возвращает video_id или None при ошибке.
    """
    token = _get_youtube_token()
    if not token:
        log.error("Нет YouTube OAuth токена")
        _print_token_instructions()
        return None

    tags = tags or ["military", "guns", "2A", "firearms", "USA", "shorts"]

    try:
        file_size = os.path.getsize(video_path)
        mime = "video/mp4"

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:15],
                "categoryId": category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        # Initiating resumable upload
        init_resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Upload-Content-Type": mime,
                "X-Upload-Content-Length": str(file_size),
            },
            json=body,
            timeout=30,
        )
        init_resp.raise_for_status()
        upload_url = init_resp.headers["Location"]

        # Загружаем чанками
        chunk_size = 10 * 1024 * 1024  # 10MB
        uploaded = 0

        with open(video_path, "rb") as f:
            while uploaded < file_size:
                chunk = f.read(chunk_size)
                end = uploaded + len(chunk) - 1

                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
                        "Content-Type": mime,
                    },
                    data=chunk,
                    timeout=300,
                )

                if resp.status_code in (200, 201):
                    video_id = resp.json().get("id")
                    yt_url = f"https://www.youtube.com/shorts/{video_id}"
                    log.info(f"YouTube upload OK: {title[:50]} → {yt_url}")
                    return video_id
                elif resp.status_code == 308:
                    uploaded += len(chunk)
                    log.info(f"YouTube upload: {uploaded / file_size * 100:.0f}%")
                else:
                    log.error(f"YouTube chunk error {resp.status_code}: {resp.text[:200]}")
                    return None

    except Exception as e:
        log.error(f"YouTube upload error: {e}")
        return None

    return None


# ─── Токены ───────────────────────────────────────────────────────────────────

def _get_google_token() -> str | None:
    """Получаем access token из сохранённого refresh token."""
    token_json = config.YOUTUBE_TOKEN_JSON
    if not token_json:
        return None

    try:
        token_data = json.loads(token_json)
        access_token = token_data.get("access_token")

        # Проверяем не истёк ли
        expires_at = token_data.get("expires_at", 0)
        if time.time() < expires_at - 60:
            return access_token

        # Обновляем через refresh token
        refresh_token = token_data.get("refresh_token")
        client_data = json.loads(config.YOUTUBE_CLIENT_SECRET_JSON or "{}")
        client_id = client_data.get("installed", {}).get("client_id") or client_data.get("web", {}).get("client_id")
        client_secret = client_data.get("installed", {}).get("client_secret") or client_data.get("web", {}).get("client_secret")

        if not refresh_token or not client_id:
            return access_token  # возвращаем что есть

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        new_data = resp.json()
        return new_data.get("access_token")

    except Exception as e:
        log.error(f"Token refresh error: {e}")
        return None


def _get_youtube_token() -> str | None:
    return _get_google_token()


def _print_token_instructions():
    log.error("""
=== КАК ПОЛУЧИТЬ YOUTUBE OAUTH TOKEN ===

1. Идёшь в Google Cloud Console → APIs & Services → Credentials
2. Создаёшь OAuth 2.0 Client ID (Desktop App)
3. Скачиваешь client_secret.json
4. Запускаешь локально:
   pip install google-auth-oauthlib
   python utils/get_token.py
5. Результат (token.json) сохраняешь в GitHub Secret YOUTUBE_TOKEN_JSON
""")


# ─── Статус публикации ────────────────────────────────────────────────────────

def save_published(video_id: str, title: str, embedding: list[float] = None):
    """Сохраняем запись об опубликованном видео для дедупликации."""
    record = {
        "video_id": video_id,
        "title": title,
        "timestamp": time.time(),
        "yt_url": f"https://www.youtube.com/shorts/{video_id}",
        "embedding": embedding,
    }

    published_file = "/tmp/gun_channel/published.json"
    os.makedirs(os.path.dirname(published_file), exist_ok=True)

    existing = []
    if os.path.exists(published_file):
        with open(published_file) as f:
            existing = json.load(f)

    existing.append(record)
    with open(published_file, "w") as f:
        json.dump(existing, f, indent=2)

    log.info(f"Записан опубликованный: {title[:50]} → {video_id}")


def load_published_embeddings() -> list[list[float]]:
    """Загружаем embeddings опубликованных видео для дедупликации."""
    published_file = "/tmp/gun_channel/published.json"
    if not os.path.exists(published_file):
        return []

    try:
        with open(published_file) as f:
            records = json.load(f)
        return [r["embedding"] for r in records if r.get("embedding")]
    except Exception:
        return []
