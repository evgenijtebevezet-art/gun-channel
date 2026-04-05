"""
search.py — мультисорсный поиск видео
Источники: YouTube CC, DVIDS, Reddit, Rumble, Odysee
"""

import os
import re
import json
import time
import random
import logging
import requests
import yt_dlp
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


@dataclass
class VideoCandidate:
    id: str
    source: str          # youtube / dvids / reddit / rumble / odysee
    url: str
    title: str
    description: str = ""
    duration_sec: int = 0
    upvotes: int = 0
    views: int = 0
    thumbnail_url: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "id": self.id, "source": self.source, "url": self.url,
            "title": self.title, "description": self.description,
            "duration_sec": self.duration_sec, "upvotes": self.upvotes,
            "views": self.views, "thumbnail_url": self.thumbnail_url,
        }


from pipeline.sources.youtube import search_youtube, search_youtube_cc, search_youtube_dark


# ─── DVIDS (Army public domain) ──────────────────────────────────────────────

def search_dvids(max_results: int = 10) -> list[VideoCandidate]:
    """DVIDS — официальный архив армии США, всё public domain."""
    candidates = []
    queries = ["weapon system", "special operations", "training exercise", "combat vehicle"]

    for query in random.sample(queries, min(2, len(queries))):
        try:
            resp = requests.get(
                "https://api.dvidshub.net/search",
                params={
                    "type": "video",
                    "query": query,
                    "rows": max_results // 2,
                    "sort": "score",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("results", []):
                duration = item.get("duration", 0) or 0
                if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                    continue

                vid_id = str(item.get("id", ""))
                url = item.get("src", {})
                if isinstance(url, dict):
                    url = url.get("hd") or url.get("sd") or ""
                elif not isinstance(url, str):
                    url = ""

                if not url:
                    page_url = f"https://www.dvidshub.net/video/{vid_id}"
                    url = page_url

                candidates.append(VideoCandidate(
                    id=f"dvids_{vid_id}",
                    source="dvids",
                    url=url,
                    title=item.get("title", ""),
                    description=item.get("description", "")[:500],
                    duration_sec=duration,
                    views=item.get("views", 0),
                    thumbnail_url=item.get("image", {}).get("thumbnail", "") if isinstance(item.get("image"), dict) else "",
                    raw=item,
                ))

            time.sleep(0.5)

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                log.warning("DVIDS API: 403 Forbidden — требуется API ключ (api.dvidshub.net/docs). Пропускаем.")
                break  # Нет смысла повторять другие запросы
            log.error(f"DVIDS search error: {e}")
        except Exception as e:
            log.error(f"DVIDS search error: {e}")

    log.info(f"DVIDS: найдено {len(candidates)} видео")
    return candidates


# ─── Reddit ──────────────────────────────────────────────────────────────────

def search_reddit(max_results: int = 15) -> list[VideoCandidate]:
    """Ищем видеопосты в gun/military сабреддитах через Reddit OAuth API."""
    candidates = []

    # Reddit требует OAuth — без ключей пропускаем, иначе получим 403
    token = _get_reddit_token()
    if not token:
        log.warning("REDDIT_CLIENT_ID/SECRET не заданы или OAuth провалился — пропускаем Reddit")
        return []

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "User-Agent": f"{config.REDDIT_USER_AGENT}",
        "Accept": "application/json",
    })
    base = "https://oauth.reddit.com"

    subs = random.sample(config.REDDIT_SUBREDDITS, min(4, len(config.REDDIT_SUBREDDITS)))

    for sub in subs:
        for sort in ["hot", "top"]:
            try:
                resp = session.get(
                    f"{base}/r/{sub}/{sort}.json",
                    params={"limit": 10, "t": "week"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                for post in data.get("data", {}).get("children", []):
                    p = post["data"]

                    # Берём только видео посты
                    is_video = (
                        p.get("is_video") or
                        p.get("post_hint") == "rich:video" or
                        "reddit_video" in p.get("secure_media", {}) or
                        any(d in p.get("url", "") for d in ["v.redd.it", "youtube.com", "youtu.be", "streamable"])
                    )
                    if not is_video:
                        continue

                    if p.get("score", 0) < 50:  # снижен порог с 100 до 50
                        continue

                    url = p.get("url", "")
                    if "v.redd.it" in url or p.get("is_video"):
                        fallback = p.get("secure_media", {}).get("reddit_video", {})
                        url = fallback.get("fallback_url", url)

                    duration = p.get("secure_media", {}).get("reddit_video", {}).get("duration", 0) or 0

                    candidates.append(VideoCandidate(
                        id=f"reddit_{p['id']}",
                        source="reddit",
                        url=url,
                        title=p.get("title", ""),
                        description=p.get("selftext", "")[:300],
                        duration_sec=duration,
                        upvotes=p.get("score", 0),
                        thumbnail_url=p.get("thumbnail", ""),
                        raw=p,
                    ))

                time.sleep(1.5)

            except Exception as e:
                log.error(f"Reddit r/{sub} error: {e}")

    log.info(f"Reddit: найдено {len(candidates)} видео")
    return candidates


# ─── YouTube CC Каналы (direct channel scraping) ─────────────────────────────

def search_youtube_channels(max_results: int = 10) -> list[VideoCandidate]:
    """Парсим конкретные CC/военные YouTube каналы напрямую без API."""
    candidates = []
    channels = getattr(config, 'YOUTUBE_CC_CHANNELS', [])
    if not channels:
        return candidates

    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'playlistend': 5,  # последние 5 видео с каждого канала
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for channel_id in random.sample(channels, min(3, len(channels))):
            try:
                url = f"https://www.youtube.com/channel/{channel_id}/videos"
                info = ydl.extract_info(url, download=False)
                for entry in (info.get("entries") or []):
                    if not entry:
                        continue
                    duration = entry.get("duration", 0) or 0
                    if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                        continue
                    candidates.append(VideoCandidate(
                        id=f"ytchan_{entry.get('id', '')}",
                        source="youtube_channel",
                        url=entry.get("url") or entry.get("webpage_url") or f"https://youtu.be/{entry.get('id','')}",
                        title=entry.get("title", ""),
                        description=entry.get("description", "")[:500],
                        duration_sec=duration,
                        views=entry.get("view_count", 0) or 0,
                        thumbnail_url=entry.get("thumbnail", ""),
                    ))
            except Exception as e:
                log.error(f"YouTube channel {channel_id} error: {e}")

    log.info(f"YouTube Channels: найдено {len(candidates)} видео")
    return candidates




# ─── Rumble ──────────────────────────────────────────────────
# ─── Rumble ──────────────────────────────────────────────────────────────────

def search_rumble(max_results: int = 10) -> list[VideoCandidate]:
    """Rumble — альтернативная площадка с gun/military контентом."""
    candidates = []
    queries = ["military weapons", "shooting range", "gun review", "2A firearms"]

    for query in random.sample(queries, min(2, len(queries))):
        try:
            resp = requests.get(
                f"https://rumble.com/api/Media/oembed.json",
                params={"url": f"https://rumble.com/search/video?q={quote_plus(query)}"},
                timeout=15,
            )
            # Rumble не имеет публичного API — используем scraping-lite через поиск
            search_resp = requests.get(
                f"https://rumble.com/search/video",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; GunChannelBot/1.0)"},
                timeout=15,
            )

            # Извлекаем video ID из страницы
            video_ids = re.findall(r'/v([a-z0-9]+)-', search_resp.text)
            titles = re.findall(r'<h3[^>]*class="[^"]*video-item--title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)<', search_resp.text)

            for i, vid_id in enumerate(video_ids[:5]):
                title = titles[i] if i < len(titles) else f"Rumble video {vid_id}"
                candidates.append(VideoCandidate(
                    id=f"rumble_{vid_id}",
                    source="rumble",
                    url=f"https://rumble.com/v{vid_id}",
                    title=title.strip(),
                    description="",
                    duration_sec=0,  # уточним при скачивании через yt-dlp
                ))

            time.sleep(1)

        except Exception as e:
            log.error(f"Rumble search error: {e}")

    log.info(f"Rumble: найдено {len(candidates)} видео")
    return candidates


# ─── Odysee ──────────────────────────────────────────────────────────────────

def search_odysee(max_results: int = 10) -> list[VideoCandidate]:
    """Odysee/LBRY — поиск через Lighthouse API (правильный публичный endpoint)."""
    candidates = []
    queries = ["military weapons", "firearms shooting", "2A guns", "tactical gear"]

    for query in random.sample(queries, min(2, len(queries))):
        try:
            # Lighthouse — официальный поисковый бэкенд Odysee
            resp = requests.get(
                "https://lighthouse.lbry.com/search",
                params={
                    "s": query,
                    "mediaType": "video",
                    "nsfw": "false",
                    "size": max_results // 2,
                    "from": 0,
                    "free_only": "true",
                },
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json()

            for item in (items if isinstance(items, list) else []):
                # Строим URL из claim_name + channel
                name = item.get("name", "")
                channel = item.get("channel_name") or item.get("channel", "")
                claim_id = item.get("claimId") or item.get("claim_id", name)

                # Канонический Odysee URL
                if channel and name:
                    canonical = f"https://odysee.com/@{channel.lstrip('@')}/{name}"
                elif name:
                    canonical = f"https://odysee.com/{name}"
                else:
                    continue

                duration = item.get("duration", 0) or 0
                if duration and not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                    continue

                candidates.append(VideoCandidate(
                    id=f"odysee_{claim_id}",
                    source="odysee",
                    url=canonical,
                    title=item.get("title") or name,
                    description=(item.get("description") or "")[:400],
                    duration_sec=duration,
                    views=item.get("view_count", 0) or 0,
                    thumbnail_url=item.get("thumbnail_url", ""),
                    raw=item,
                ))

            time.sleep(0.5)

        except Exception as e:
            log.error(f"Odysee search error: {e}")

    log.info(f"Odysee: найдено {len(candidates)} видео")
    return candidates


# ─── Главная функция ─────────────────────────────────────────────────────────

def search_all(max_total: int = None) -> list[VideoCandidate]:
    """Запускает все источники и возвращает объединённый список кандидатов."""
    max_total = max_total or config.MAX_CANDIDATES_PER_RUN

    per_source = max(5, max_total // 5)

    all_candidates = []
    all_candidates += search_youtube_cc(per_source)
    all_candidates += search_youtube_dark(per_source)
    all_candidates += search_youtube_channels(per_source)
    all_candidates += search_dvids(per_source)
    all_candidates += search_reddit(per_source)
    all_candidates += search_rumble(per_source)
    all_candidates += search_odysee(per_source)

    # Дедупликация по URL
    seen_urls = set()
    unique = []
    for c in all_candidates:
        key = c.url.split("?")[0].rstrip("/")
        if key not in seen_urls:
            seen_urls.add(key)
            unique.append(c)

    log.info(f"Итого кандидатов (после дедупа): {len(unique)}")
    return unique[:max_total]


# ─── Хелперы ─────────────────────────────────────────────────────────────────

def _parse_iso_duration(iso: str) -> int:
    """PT1H2M3S → секунды."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
    if not match:
        return 0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s


def _get_reddit_token() -> Optional[str]:
    if not config.REDDIT_CLIENT_ID or not config.REDDIT_CLIENT_SECRET:
        return None
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": config.REDDIT_USER_AGENT},
            timeout=10,
        )
        return resp.json().get("access_token")
    except Exception:
        return None


if __name__ == "__main__":
    results = search_all(max_total=20)
    for r in results:
        print(f"[{r.source}] {r.title[:60]} | {r.duration_sec}s | {r.url}")
