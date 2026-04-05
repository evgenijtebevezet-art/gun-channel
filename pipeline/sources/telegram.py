"""
pipeline/sources/telegram.py — поиск видео в Telegram каналах
Режим 1: без API — парсим публичные t.me/s/
Режим 2: через Telethon MTProto API (api_id + api_hash из my.telegram.org)
"""

import re
import time
import logging
import requests
from pipeline.search import VideoCandidate

log = logging.getLogger(__name__)

DEFAULT_CHANNELS = [
    "militarymemes",
    "combatfootage",
    "warfootage",
    "tacticalgear",
    "gunsdaily",
]


def search_telegram_public(
    channels: list[str] = None,
    max_per_channel: int = 5,
) -> list[VideoCandidate]:
    """Парсим публичные каналы через t.me/s/ — без API ключей."""
    channels = channels or DEFAULT_CHANNELS
    candidates = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for channel in channels:
        try:
            resp = requests.get(f"https://t.me/s/{channel}", headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            html = resp.text
            post_ids = re.findall(r'data-post="([^"]+)"', html)
            post_texts = re.findall(
                r'<div class="tgme_widget_message_text[^"]*">(.*?)</div>',
                html, re.DOTALL
            )
            thumb_urls = re.findall(
                r'background-image:url\(\'([^\']+)\'\)', html
            )

            count = 0
            for i, post_id_full in enumerate(post_ids[:25]):
                if count >= max_per_channel:
                    break

                # Проверяем есть ли видео в этом посте
                start = html.find(f'data-post="{post_id_full}"')
                end = html.find('data-post=', start + 10) if start != -1 else -1
                block = html[start:end] if start != -1 and end != -1 else ""

                if "video" not in block.lower() and "mp4" not in block.lower():
                    continue

                parts = post_id_full.split("/")
                ch_name = parts[0] if len(parts) > 1 else channel
                post_num = parts[-1]

                text = re.sub(r'<[^>]+>', '', post_texts[i]).strip()[:300] if i < len(post_texts) else ""
                post_url = f"https://t.me/{ch_name}/{post_num}"

                candidates.append(VideoCandidate(
                    id=f"tg_{ch_name}_{post_num}",
                    source="telegram",
                    url=post_url,
                    title=text[:80] or f"@{ch_name} video #{post_num}",
                    description=text,
                    duration_sec=0,
                    thumbnail_url=thumb_urls[i] if i < len(thumb_urls) else "",
                ))
                count += 1

            log.info(f"Telegram @{channel}: найдено {count} видео")
            time.sleep(1.5)

        except Exception as e:
            log.error(f"Telegram @{channel} error: {e}")

    return candidates


def search_telegram_api(
    api_id: str,
    api_hash: str,
    channels: list[str] = None,
    max_per_channel: int = 10,
) -> list[VideoCandidate]:
    """Через Telethon MTProto — полный доступ к медиа."""
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        log.warning("pip install telethon — переключаемся на публичный парсинг")
        return search_telegram_public(channels, max_per_channel)

    channels = channels or DEFAULT_CHANNELS
    candidates = []

    with TelegramClient("gun_ch_session", int(api_id), api_hash) as client:
        for channel in channels:
            try:
                entity = client.get_entity(channel)
                for msg in client.get_messages(entity, limit=max_per_channel * 3):
                    if not msg.media or not hasattr(msg.media, "document"):
                        continue

                    doc = msg.media.document
                    is_video = any(
                        getattr(a, "mime_type", "").startswith("video/")
                        or hasattr(a, "duration")
                        for a in getattr(doc, "attributes", [])
                    )
                    if not is_video:
                        continue

                    duration = next(
                        (int(a.duration) for a in doc.attributes if hasattr(a, "duration")), 0
                    )
                    text = (msg.text or "")[:300]

                    candidates.append(VideoCandidate(
                        id=f"tg_{channel}_{msg.id}",
                        source="telegram",
                        url=f"https://t.me/{channel}/{msg.id}",
                        title=text[:80] or f"@{channel} #{msg.id}",
                        description=text,
                        duration_sec=duration,
                    ))

                    if len([c for c in candidates if c.source == "telegram"]) >= max_per_channel:
                        break

                time.sleep(1)
            except Exception as e:
                log.error(f"Telethon @{channel}: {e}")

    return candidates
