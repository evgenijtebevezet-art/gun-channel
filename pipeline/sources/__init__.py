"""
pipeline/sources/telegram.py — поиск видео в Telegram каналах
Использует Telethon (MTProto) или pyrogram.
Без API — парсим через публичный t.me preview.
"""

import re
import time
import logging
import requests
from pipeline.search import VideoCandidate

log = logging.getLogger(__name__)

# Публичные Telegram каналы с military/gun контентом
DEFAULT_CHANNELS = [
    "militarymemes",
    "combatfootage",
    "warfootage",
    "tacticalgear",
    "gunsdaily",
    "2ndamendment",
]


def search_telegram_public(
    channels: list[str] = None,
    max_per_channel: int = 5,
) -> list[VideoCandidate]:
    """
    Парсим публичные Telegram каналы через t.me/s/ (без API ключей).
    Находим посты с видео.
    """
    channels = channels or DEFAULT_CHANNELS
    candidates = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for channel in channels:
        try:
            resp = requests.get(
                f"https://t.me/s/{channel}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                log.debug(f"Telegram {channel}: {resp.status_code}")
                continue

            html = resp.text

            # Ищем посты с видео
            # Telegram web показывает video через <video> теги
            video_blocks = re.findall(
                r'<div class="tgme_widget_message[^"]*"[^>]*data-post="([^"]+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
                html,
                re.DOTALL,
            )

            # Альтернативный паттерн для видео постов
            post_ids = re.findall(r'data-post="([^"]+)"', html)
            post_texts = re.findall(
                r'<div class="tgme_widget_message_text[^"]*">(.*?)</div>',
                html,
                re.DOTALL,
            )
            video_urls_in_page = re.findall(r'<video[^>]+src="([^"]+)"', html)
            thumb_urls = re.findall(r'<i class="[^"]*tgme_widget_message_video_thumb[^"]*"[^>]*style="[^"]*url\(\'([^\']+)\'\)', html)

            # Ищем посты где есть видео-контент
            has_video_posts = re.findall(
                r'data-post="([^/]+/(\d+))"[^\n]*\n(?:.*\n){0,20}?.*(?:video|\.mp4|tgme_widget_message_video)',
                html,
            )

            count = 0
            for i, post_id_full in enumerate(post_ids[:20]):
                if count >= max_per_channel:
                    break

                channel_name = post_id_full.split("/")[0] if "/" in post_id_full else channel
                post_num = post_id_full.split("/")[-1] if "/" in post_id_full else str(i)

                # Текст поста
                text = post_texts[i].strip() if i < len(post_texts) else ""
                text = re.sub(r'<[^>]+>', '', text)[:300]

                if not text and not video_urls_in_page:
                    continue

                # Проверяем что это видео пост (по контексту в html)
                post_block_match = re.search(
                    rf'data-post="{re.escape(post_id_full)}".*?(?=data-post="|$)',
                    html,
                    re.DOTALL,
                )
                if not post_block_match:
                    continue

                post_block = post_block_match.group()
                if "video" not in post_block.lower() and "mp4" not in post_block.lower():
                    continue

                # Формируем прямую ссылку на пост
                post_url = f"https://t.me/{channel_name}/{post_num}"

                candidates.append(VideoCandidate(
                    id=f"tg_{channel_name}_{post_num}",
                    source="telegram",
                    url=post_url,
                    title=text[:80] or f"[{channel_name}] video post",
                    description=text,
                    duration_sec=0,
                    upvotes=0,
                    thumbnail_url=thumb_urls[i] if i < len(thumb_urls) else "",
                ))
                count += 1

            log.info(f"Telegram @{channel}: {count} видео")
            time.sleep(1.5)

        except Exception as e:
            log.error(f"Telegram @{channel} error: {e}")

    return candidates


def search_telegram_with_api(
    api_id: str,
    api_hash: str,
    channels: list[str] = None,
    max_per_channel: int = 10,
) -> list[VideoCandidate]:
    """
    Через Telethon API — нужны api_id и api_hash от my.telegram.org.
    Даёт доступ к реальным видеофайлам.
    """
    try:
        from telethon.sync import TelegramClient
        from telethon.tl.types import MessageMediaDocument
    except ImportError:
        log.warning("Telethon не установлен. pip install telethon")
        return search_telegram_public(channels, max_per_channel)

    channels = channels or DEFAULT_CHANNELS
    candidates = []

    try:
        with TelegramClient("gun_channel_session", int(api_id), api_hash) as client:
            for channel in channels:
                try:
                    entity = client.get_entity(channel)
                    messages = client.get_messages(entity, limit=max_per_channel)

                    for msg in messages:
                        if not msg.media:
                            continue

                        is_video = (
                            hasattr(msg.media, "document") and
                            any(
                                getattr(attr, "mime_type", "").startswith("video/")
                                for attr in getattr(msg.media.document, "attributes", [])
                            )
                        )

                        if not is_video:
                            continue

                        # Длительность видео
                        duration = 0
                        for attr in getattr(msg.media.document, "attributes", []):
                            if hasattr(attr, "duration"):
                                duration = int(attr.duration)
                                break

                        text = (msg.text or "")[:300]
                        post_url = f"https://t.me/{channel}/{msg.id}"

                        candidates.append(VideoCandidate(
                            id=f"tg_{channel}_{msg.id}",
                            source="telegram",
                            url=post_url,
                            title=text[:80] or f"[{channel}] video",
                            description=text,
                            duration_sec=duration,
                            upvotes=getattr(msg, "reactions", 0),
                        ))

                    log.info(f"Telegram API @{channel}: {len(candidates)} видео")
                    time.sleep(1)

                except Exception as e:
                    log.error(f"Telegram API channel {channel}: {e}")

    except Exception as e:
        log.error(f"Telethon client error: {e}")

    return candidates
