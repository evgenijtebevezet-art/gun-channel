"""
pipeline/sources/youtube.py — поиск видео с YouTube
  search_youtube_cc   — через YouTube Data API v3 (требует YOUTUBE_API_KEY)
  search_youtube_dark — через yt-dlp без API ключа
  download_youtube_video — скачивание через yt-dlp
"""

import os
import re
import random
import logging
import requests
import yt_dlp

import config
from pipeline.search import VideoCandidate

log = logging.getLogger(__name__)


# ─── CC поиск через YouTube Data API v3 ──────────────────────────────────────

def search_youtube_cc(max_results: int = 10) -> list[VideoCandidate]:
    """YouTube Data API v3 — CC/military каналы. Требует YOUTUBE_API_KEY."""
    if not config.YOUTUBE_API_KEY:
        log.warning("YOUTUBE_API_KEY не задан, пропускаем YouTube CC поиск")
        return []

    candidates = []
    queries = random.sample(config.YOUTUBE_SEARCH_QUERIES, min(2, len(config.YOUTUBE_SEARCH_QUERIES)))

    for query in queries:
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": config.YOUTUBE_API_KEY,
                    "q": query,
                    "type": "video",
                    "videoDuration": "short",
                    "videoLicense": "creativeCommon",
                    "maxResults": max(2, max_results // 2),
                    "order": "viewCount",
                    "part": "id,snippet",
                    "safeSearch": "none",
                },
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])

            video_ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
            details = {}
            if video_ids:
                det_resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={
                        "key": config.YOUTUBE_API_KEY,
                        "id": ",".join(video_ids),
                        "part": "contentDetails,statistics",
                    },
                    timeout=15,
                )
                det_resp.raise_for_status()
                details = {v["id"]: v for v in det_resp.json().get("items", [])}

            for item in items:
                vid_id = item.get("id", {}).get("videoId", "")
                if not vid_id:
                    continue
                det = details.get(vid_id, {})
                duration = _parse_iso_duration(det.get("contentDetails", {}).get("duration", "PT0S"))
                if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                    continue
                snippet = item.get("snippet", {})
                stats = det.get("statistics", {})
                candidates.append(VideoCandidate(
                    id=f"ytcc_{vid_id}",
                    source="youtube_cc",
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:500],
                    duration_sec=duration,
                    views=int(stats.get("viewCount", 0) or 0),
                    thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                ))

        except Exception as e:
            log.error(f"YouTube CC search error for '{query}': {e}")

    log.info(f"YouTube CC: найдено {len(candidates)} видео")
    return candidates


# ─── Dark поиск через yt-dlp (без API ключа) ──────────────────────────────────

def search_youtube_dark(max_results: int = 10) -> list[VideoCandidate]:
    """Поиск через yt-dlp — работает без API ключа."""
    candidates = []
    queries = random.sample(config.YOUTUBE_SEARCH_QUERIES, min(3, len(config.YOUTUBE_SEARCH_QUERIES)))

    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "age_limit": 18,
        "socket_timeout": 20,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query in queries:
            try:
                max_per_query = max(2, max_results // len(queries))
                info = ydl.extract_info(f"ytsearch{max_per_query}:{query}", download=False)
                for entry in (info.get("entries") or []):
                    if not entry:
                        continue
                    duration = entry.get("duration", 0) or 0
                    if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                        continue
                    vid_id = entry.get("id", "")
                    candidates.append(VideoCandidate(
                        id=f"ytdark_{vid_id}",
                        source="youtube_ytdlp",
                        # Строим полный URL — flat search может вернуть только ID
                        url=f"https://www.youtube.com/watch?v={vid_id}" if vid_id
                            else (entry.get("webpage_url") or entry.get("url") or ""),
                        title=entry.get("title", ""),
                        description=(entry.get("description") or "")[:500],
                        duration_sec=duration,
                        views=entry.get("view_count", 0) or 0,
                        thumbnail_url=entry.get("thumbnail", ""),
                        raw=entry,
                    ))
            except Exception as e:
                log.error(f"YouTube Dark search error for '{query}': {e}")

    log.info(f"YouTube Dark: найдено {len(candidates)} видео")
    return candidates


# ─── Скачивание ───────────────────────────────────────────────────────────────

def download_youtube_video(url: str, output_path: str, max_sec: int = 180) -> bool:
    """Скачивание видео через yt-dlp."""
    try:
        def _match_filter(info, *, incomplete):
            duration = info.get("duration") or 0
            if duration and duration > max_sec:
                return f"Skipping: duration {duration}s > {max_sec}s"
            return None

        ydl_opts = {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[ext=mp4]/best",
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "match_filter": _match_filter,
            "socket_timeout": 30,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return os.path.exists(output_path)
    except Exception as e:
        log.error(f"Download error for {url}: {e}")
        return False


# ─── Legacy alias ─────────────────────────────────────────────────────────────

def search_youtube(max_results: int = 10, queries: list[str] = None) -> list[VideoCandidate]:
    """Legacy alias → search_youtube_dark."""
    return search_youtube_dark(max_results)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_iso_duration(iso: str) -> int:
    """PT1H2M3S → секунды."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not match:
        return 0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s
