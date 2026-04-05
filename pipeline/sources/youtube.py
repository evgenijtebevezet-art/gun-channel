"""
pipeline/sources/youtube.py — поиск и скачивание видео с YouTube
Использует yt-dlp (Python API) для поиска и загрузки.
"""

import os
import random
import logging
import yt_dlp
from typing import Optional

import config
from pipeline.search import VideoCandidate

log = logging.getLogger(__name__)

def search_youtube(max_results: int = 10, queries: list[str] = None) -> list[VideoCandidate]:
    """
    Поиск видео на YouTube по заданным запросам через yt-dlp (dark search).
    """
    candidates = []
    queries = queries or config.YOUTUBE_SEARCH_QUERIES
    # Берем случайные 3 запроса (или меньше, если всего запросов меньше)
    selected_queries = random.sample(queries, min(3, len(queries)))

    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'default_search': 'ytsearch',
        'age_limit': 18,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query in selected_queries:
            try:
                # Добавляем " short" для поиска коротких форматов
                search_query = f"{query} short"
                max_per_query = max_results // len(selected_queries) + 1
                q = f"ytsearch{max_per_query}:{search_query}"
                
                info = ydl.extract_info(q, download=False)
                
                for entry in info.get("entries", []):
                    if not entry:
                        continue
                        
                    duration = entry.get("duration", 0) or 0
                    if not (config.VIDEO_MIN_DURATION_SEC <= duration <= config.VIDEO_MAX_DURATION_SEC):
                        continue
                        
                    candidates.append(VideoCandidate(
                        id=f"yt_{entry.get('id', '')}",
                        source="youtube",
                        url=entry.get("url") or entry.get("webpage_url") or "",
                        title=entry.get("title", ""),
                        description=entry.get("description", "")[:500],
                        duration_sec=duration,
                        views=entry.get("view_count", 0),
                        thumbnail_url=entry.get("thumbnail", ""),
                        raw=entry
                    ))
            except Exception as e:
                log.error(f"YouTube search error for '{query}': {e}")

    log.info(f"YouTube: найдено {len(candidates)} видео")
    return candidates

def download_youtube_video(url: str, output_path: str, max_sec: int = 180) -> bool:
    """
    Скачивание видео через yt-dlp Python API.
    """
    try:
        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'match_filter': yt_dlp.utils.match_filter_func(
                f"duration <= {max_sec}"
            ),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return os.path.exists(output_path)
    except Exception as e:
        log.error(f"Download error for {url}: {e}")
        return False
