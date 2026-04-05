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


# ─── YouTube CC ──────────────────────────────────────────────────────────────

def search_youtube_cc(max_results: int = 10) -> list[VideoCandidate]:
    """Поиск видео с Creative Commons лицензией через YouTube Data API."""
    if not config.YOUTUBE_API_KEY:
        log.warning("YOUTUBE_API_KEY не задан, пропускаем YouTube")
        return []

    candidates = []
    queries = random.sample(config.YOUTUBE_SEARCH_QUERIES, min(3, len(config.YOUTUBE_SEARCH_QUERIES)))

    for query in queries:
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "videoLicense": "creativeCommon",
                    "videoDuration": "short",  # до 4 минут
                    "maxResults": max_results // len(queries) + 1,
                    "key": config.YOUTUBE_API_KEY,
                    "relevanceLanguage": "en",
                    "regionCode": "US",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            video_ids = [i["id"]["videoId"] for i in data.get("items", [])]
            if not video_ids:
                continue

            # Получаем длительность через videos.list
            details_resp = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "contentDetails,statistics",
                    "id": ",".join(video_ids),
                    "key": config.YOUTUBE_API_KEY,
                },
                timeout=15,
            )
            details_resp.raise_for_status()
            details = {v["id"]: v for v in details_resp.json().get("items", [])}

            for item in data.get("items", []):
                vid_id = item["id"]["videoId"]
                snippet = item["snippet"]
                detail = details.get(vid_id, {})

                duration = _parse_iso_duration(
                    detail.get("contentDetails", {}).get("duration", "PT0S")
                )
                if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                    continue

                stats = detail.get("statistics", {})
                candidates.append(VideoCandidate(
                    id=f"yt_{vid_id}",
                    source="youtube",
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:500],
                    duration_sec=duration,
                    views=int(stats.get("viewCount", 0)),
                    thumbnail_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    raw=item,
                ))

            time.sleep(0.5)

        except Exception as e:
            log.error(f"YouTube search error for '{query}': {e}")

    log.info(f"YouTube CC: найдено {len(candidates)} видео")
    return candidates


# ─── YouTube Dark (через yt-dlp, без API ключей) ─────────────────────────────

def search_youtube_dark(max_results: int = 10) -> list[VideoCandidate]:
    """Тёмный парсинг YouTube через yt-dlp. Работает без ключей."""
    candidates = []
    queries = random.sample(config.YOUTUBE_SEARCH_QUERIES, min(3, len(config.YOUTUBE_SEARCH_QUERIES)))

    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'default_search': 'ytsearch',
        'age_limit': 18,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query in queries:
            try:
                # Ищем короткие милитари видео
                q = f"ytsearch{max_results // len(queries) + 1}:{query} short"
                info = ydl.extract_info(q, download=False)
                
                for entry in info.get("entries", []):
                    if not entry:
                        continue
                    
                    duration = entry.get("duration", 0) or 0
                    if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                        continue
                    
                    candidates.append(VideoCandidate(
                        id=f"ytdark_{entry.get('id', '')}",
                        source="youtube_ytdlp",
                        url=entry.get("url") or entry.get("webpage_url") or "",
                        title=entry.get("title", ""),
                        description=entry.get("description", "")[:500],
                        duration_sec=duration,
                        views=entry.get("view_count", 0),
                        thumbnail_url=entry.get("thumbnail", ""),
                    ))
            except Exception as e:
                log.error(f"yt-dlp dark search error for '{query}': {e}")

    log.info(f"YouTube Dark: найдено {len(candidates)} видео")
    return candidates


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

        except Exception as e:
            log.error(f"DVIDS search error: {e}")

    log.info(f"DVIDS: найдено {len(candidates)} видео")
    return candidates


# ─── Reddit ──────────────────────────────────────────────────────────────────

def search_reddit(max_results: int = 15) -> list[VideoCandidate]:
    """Ищем видеопосты в gun/military сабреддитах через Reddit JSON API."""
    candidates = []
    # Мимикрируем под обычный браузер (Dark Parsing)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # Авторизация если есть ключи, иначе публичный JSON
    token = _get_reddit_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        base = "https://oauth.reddit.com"
    else:
        base = "https://www.reddit.com"

    subs = random.sample(config.REDDIT_SUBREDDITS, min(4, len(config.REDDIT_SUBREDDITS)))

    for sub in subs:
        for sort in ["hot", "top"]:
            try:
                resp = requests.get(
                    f"{base}/r/{sub}/{sort}.json",
                    params={"limit": 10, "t": "week"},
                    headers=headers,
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

                    if p.get("score", 0) < 100:
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

                time.sleep(1)

            except Exception as e:
                log.error(f"Reddit r/{sub} error: {e}")

    log.info(f"Reddit: найдено {len(candidates)} видео")
    return candidates


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
    """Odysee — децентрализованный хостинг, много gun/military контента."""
    candidates = []
    queries = ["military", "firearms", "shooting", "2A", "tactical"]

    for query in random.sample(queries, min(2, len(queries))):
        try:
            resp = requests.post(
                "https://api.odysee.com/api/v1/search",
                json={
                    "method": "search",
                    "params": {
                        "q": query,
                        "claim_type": "stream",
                        "media_type": "video",
                        "limit": max_results // 2,
                        "order_by": "top",
                    },
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("data", {}).get("documents", []) or []:
                duration = item.get("value", {}).get("video", {}).get("duration", 0) or 0
                if duration and not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                    continue

                claim_id = item.get("claim_id", item.get("name", ""))
                canonical = item.get("canonical_url", "").replace("lbry://", "https://odysee.com/")

                candidates.append(VideoCandidate(
                    id=f"odysee_{claim_id}",
                    source="odysee",
                    url=canonical,
                    title=item.get("value", {}).get("title", item.get("name", "")),
                    description=item.get("value", {}).get("description", "")[:400],
                    duration_sec=duration,
                    views=item.get("meta", {}).get("effective_amount", 0),
                    thumbnail_url=item.get("value", {}).get("thumbnail", {}).get("url", ""),
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
