"""
utils/history.py — хранение истории опубликованных видео в Google Drive.
Между запусками GitHub Actions файл published.json синхронизируется с Drive.
Это нужно для дедупликации — без истории бот может публиковать одно и то же.
"""

import os
import json
import time
import logging
import requests
from pathlib import Path

log = logging.getLogger(__name__)

LOCAL_HISTORY = "/tmp/gun_channel/published.json"
DRIVE_FILENAME = "gun_channel_published_history.json"


def load_history(google_token: str = None, drive_folder_id: str = None) -> list[dict]:
    """
    Загружает историю опубликованных видео.
    Сначала пробует с Drive, потом локальный файл.
    """
    # Пробуем скачать с Google Drive
    if google_token and drive_folder_id:
        history = _load_from_drive(google_token, drive_folder_id)
        if history is not None:
            _save_local(history)
            return history

    # Локальный файл
    return _load_local()


def save_history(
    records: list[dict],
    google_token: str = None,
    drive_folder_id: str = None,
):
    """Сохраняет историю локально и на Drive."""
    _save_local(records)
    if google_token and drive_folder_id:
        _save_to_drive(records, google_token, drive_folder_id)


def add_record(
    video_id: str,
    title: str,
    source_url: str,
    embedding: list[float] = None,
    google_token: str = None,
    drive_folder_id: str = None,
):
    """Добавляет одну запись в историю и синхронизирует."""
    records = load_history(google_token, drive_folder_id)

    new_record = {
        "video_id": video_id,
        "title": title,
        "source_url": source_url,
        "yt_url": f"https://www.youtube.com/shorts/{video_id}",
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timestamp": time.time(),
        "embedding": embedding,
    }

    records.append(new_record)
    save_history(records, google_token, drive_folder_id)
    log.info(f"История обновлена: {title[:50]} → {video_id}")


def get_published_embeddings(records: list[dict] = None) -> list[list[float]]:
    """Возвращает embeddings всех опубликованных видео для дедупликации."""
    if records is None:
        records = _load_local()
    return [r["embedding"] for r in records if r.get("embedding")]


def get_published_urls(records: list[dict] = None) -> set[str]:
    """Возвращает набор source URL уже опубликованных видео."""
    if records is None:
        records = _load_local()
    return {r.get("source_url", "") for r in records}


# ─── Drive хелперы ───────────────────────────────────────────────────────────

def _find_drive_file(token: str, folder_id: str) -> str | None:
    """Ищем файл истории на Drive по имени."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": f"name='{DRIVE_FILENAME}' and '{folder_id}' in parents and trashed=false",
                "fields": "files(id, name, modifiedTime)",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        log.error(f"Drive search error: {e}")
        return None


def _load_from_drive(token: str, folder_id: str) -> list[dict] | None:
    """Скачиваем историю с Drive."""
    file_id = _find_drive_file(token, folder_id)
    if not file_id:
        log.info("Файл истории на Drive не найден — начинаем с нуля")
        return []

    try:
        resp = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"История загружена с Drive: {len(data)} записей")
        return data
    except Exception as e:
        log.error(f"Drive download error: {e}")
        return None


def _save_to_drive(records: list[dict], token: str, folder_id: str):
    """Загружаем/обновляем историю на Drive."""
    content = json.dumps(records, indent=2, ensure_ascii=False).encode("utf-8")
    file_id = _find_drive_file(token, folder_id)

    try:
        if file_id:
            # Обновляем существующий файл
            resp = requests.patch(
                f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
                params={"uploadType": "media"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=content,
                timeout=30,
            )
        else:
            # Создаём новый
            boundary = "boundary_gun_channel"
            body = (
                f"--{boundary}\r\n"
                f'Content-Type: application/json\r\n\r\n'
                f'{json.dumps({"name": DRIVE_FILENAME, "parents": [folder_id]})}\r\n'
                f"--{boundary}\r\n"
                f"Content-Type: application/json\r\n\r\n"
            ).encode() + content + f"\r\n--{boundary}--".encode()

            resp = requests.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "multipart"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                data=body,
                timeout=30,
            )

        resp.raise_for_status()
        log.info(f"История сохранена на Drive ({len(records)} записей)")

    except Exception as e:
        log.error(f"Drive save error: {e}")


def _load_local() -> list[dict]:
    if os.path.exists(LOCAL_HISTORY):
        try:
            with open(LOCAL_HISTORY) as f:
                data = json.load(f)
            log.info(f"Локальная история: {len(data)} записей")
            return data
        except Exception:
            pass
    return []


def _save_local(records: list[dict]):
    os.makedirs(os.path.dirname(LOCAL_HISTORY), exist_ok=True)
    with open(LOCAL_HISTORY, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
