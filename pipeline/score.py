"""
score.py — AI скоринг видеокандидатов
Шаги:
  1. Groq Llama 8B — быстрый pre-фильтр по заголовку/описанию
  2. Gemini Embedding 2 — дедупликация с уже опубликованными
  3. Groq Whisper — транскрипция (если есть аудио)
  4. Gemma 4 31B — анализ транскрипта
  5. Qwen3.5-397B (NVIDIA) — vision-скоринг по кадрам (топ кандидаты)
  6. Groq Llama 3.3 70B — финальный viral score
"""

import os
import json
import time
import base64
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests
from openai import OpenAI  # совместимо с Groq и NVIDIA

import config
from pipeline.search import VideoCandidate

log = logging.getLogger(__name__)

# ─── Клиенты ─────────────────────────────────────────────────────────────────

groq_client = OpenAI(
    api_key=config.GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

nvidia_client = OpenAI(
    api_key=config.NVIDIA_API_KEY,
    base_url="https://integrate.api.nvidia.com/v1",
)


# ─── ЭТАП 1: Быстрый pre-фильтр по тексту (Groq Llama 8B) ───────────────────

def prefilter_by_text(candidates: list[VideoCandidate]) -> list[VideoCandidate]:
    """Отсеиваем нерелевантный контент по заголовку/описанию — дёшево и быстро."""
    passed = []
    for c in candidates:
        try:
            resp = groq_client.chat.completions.create(
                model=config.GROQ_FAST_MODEL,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Rate this video for a US military/gun YouTube channel (1-10).\n"
                        f"Title: {c.title}\n"
                        f"Description: {c.description[:300]}\n"
                        f"Source: {c.source}\n\n"
                        "Reply with ONLY a number 1-10. "
                        "10=perfect (action, weapons, military, ranch guns). "
                        "1=completely irrelevant."
                    ),
                }],
                max_tokens=5,
                temperature=0,
            )
            score_str = resp.choices[0].message.content.strip()
            score = float("".join(c2 for c2 in score_str if c2.isdigit() or c2 == "."))
            c.raw["prefilter_score"] = score
            if score >= 5:
                passed.append(c)
            else:
                log.debug(f"Pre-filter отсеял [{score}]: {c.title[:50]}")

        except Exception as e:
            log.error(f"Pre-filter error for {c.id}: {e}")
            passed.append(c)  # при ошибке — оставляем

        time.sleep(0.1)

    log.info(f"Pre-filter: {len(candidates)} → {len(passed)} кандидатов")
    return passed


# ─── ЭТАП 2: Дедупликация через Gemini Embedding ─────────────────────────────

def deduplicate_with_embeddings(
    candidates: list[VideoCandidate],
    published_embeddings: list[list[float]] = None,
    threshold: float = 0.88,
) -> list[VideoCandidate]:
    """Убираем видео похожие на уже опубликованные (по embedding cosine sim)."""
    if not config.GEMINI_API_KEY or not published_embeddings:
        return candidates

    import math

    def cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x**2 for x in a))
        nb = math.sqrt(sum(x**2 for x in b))
        return dot / (na * nb + 1e-9)

    unique = []
    for c in candidates:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/{config.GEMINI_EMBED_MODEL}:embedContent",
                params={"key": config.GEMINI_API_KEY},
                json={
                    "model": config.GEMINI_EMBED_MODEL,
                    "content": {"parts": [{"text": f"{c.title} {c.description}"}]},
                },
                timeout=15,
            )
            resp.raise_for_status()
            emb = resp.json()["embedding"]["values"]
            c.raw["embedding"] = emb

            max_sim = max((cosine_sim(emb, pub) for pub in published_embeddings), default=0)
            if max_sim < threshold:
                unique.append(c)
            else:
                log.info(f"Дубль (sim={max_sim:.2f}): {c.title[:50]}")

            time.sleep(0.3)

        except Exception as e:
            log.error(f"Embedding error for {c.id}: {e}")
            unique.append(c)

    log.info(f"Дедупликация: {len(candidates)} → {len(unique)}")
    return unique


# ─── ЭТАП 3: Транскрипция через Groq Whisper ─────────────────────────────────

def transcribe_audio(video_path: str) -> str:
    """Транскрибирует аудио из видеофайла через Groq Whisper."""
    if not os.path.exists(video_path):
        return ""

    # Извлекаем аудио в mp3
    audio_path = video_path.replace(".mp4", "_audio.mp3").replace(".webm", "_audio.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            audio_path
        ], capture_output=True, timeout=60)

        if not os.path.exists(audio_path):
            return ""

        with open(audio_path, "rb") as f:
            resp = groq_client.audio.transcriptions.create(
                model=config.GROQ_WHISPER_MODEL,
                file=("audio.mp3", f, "audio/mpeg"),
                language="en",
            )
        return resp.text

    except Exception as e:
        log.error(f"Transcription error: {e}")
        return ""
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


# ─── ЭТАП 4: Анализ транскрипта (Gemma 4 31B) ────────────────────────────────

def analyze_transcript(transcript: str, title: str) -> dict:
    """Gemma 4 31B анализирует транскрипт (Unlimited TPM)."""
    if not transcript or not config.GEMINI_API_KEY:
        return {"summary": "", "highlights": [], "quality": 5}

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:generateContent",
            params={"key": config.GEMINI_API_KEY},
            json={
                "contents": [{
                    "parts": [{
                        "text": (
                            f"Analyze this video transcript for a US military/gun YouTube channel.\n"
                            f"Title: {title}\n"
                            f"Transcript: {transcript[:2000]}\n\n"
                            "Return JSON only:\n"
                            '{"summary": "2-3 sentence summary", '
                            '"highlights": ["key moment 1", "key moment 2"], '
                            '"has_action": true/false, '
                            '"has_humor": true/false, '
                            '"quality": 1-10}'
                        )
                    }]
                }],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # Парсим JSON из ответа
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        log.error(f"Transcript analysis error: {e}")

    return {"summary": "", "highlights": [], "quality": 5}


# ─── ЭТАП 5: Vision-скоринг кадров (Qwen3.5-397B via NVIDIA NIM) ─────────────

def score_frames_with_qwen(video_path: str, title: str) -> float:
    """Извлекаем 5 кадров и скармливаем Qwen для visual scoring."""
    if not config.NVIDIA_API_KEY or not os.path.exists(video_path):
        return 5.0

    frames = _extract_frames(video_path, count=5)
    if not frames:
        return 5.0

    # Кодируем кадры в base64
    frame_contents = []
    for frame_path in frames:
        try:
            with open(frame_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            frame_contents.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        except Exception:
            pass

    if not frame_contents:
        return 5.0

    try:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    f"You are scoring video frames for a US military and gun culture YouTube Shorts channel.\n"
                    f"Video title: '{title}'\n"
                    f"These are {len(frame_contents)} frames from the video.\n\n"
                    "Score 1-10 based on:\n"
                    "- Visual excitement (action, explosions, cool weapons, funny moments)\n"
                    "- Relevance to US military/guns/2A audience\n"
                    "- Thumbnail worthiness\n"
                    "- Viral potential for YouTube Shorts\n\n"
                    "Reply ONLY with a number 1-10."
                )},
                *frame_contents,
            ],
        }]

        resp = nvidia_client.chat.completions.create(
            model=config.NVIDIA_VISION_MODEL,
            messages=messages,
            max_tokens=10,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        score_str = resp.choices[0].message.content.strip()
        score = float("".join(c for c in score_str if c.isdigit() or c == "."))
        log.info(f"Qwen vision score для '{title[:40]}': {score}")
        return min(10.0, max(1.0, score))

    except Exception as e:
        log.error(f"Qwen vision scoring error: {e}")
        return 5.0
    finally:
        for f in frames:
            try:
                os.remove(f)
            except Exception:
                pass


# ─── ЭТАП 6: Финальный viral score (Groq Llama 3.3 70B) ─────────────────────

def final_score(
    candidate: VideoCandidate,
    transcript_analysis: dict,
    vision_score: float,
) -> float:
    """Финальная оценка с учётом всех сигналов."""
    try:
        summary = transcript_analysis.get("summary", "")
        highlights = transcript_analysis.get("highlights", [])
        prefilter = candidate.raw.get("prefilter_score", 5)

        resp = groq_client.chat.completions.create(
            model=config.GROQ_LLM_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    f"Score this video for US military/gun YouTube Shorts channel (1-10).\n\n"
                    f"Title: {candidate.title}\n"
                    f"Source: {candidate.source}\n"
                    f"Duration: {candidate.duration_sec}s\n"
                    f"Views/Upvotes: {candidate.views or candidate.upvotes}\n"
                    f"Content summary: {summary}\n"
                    f"Key highlights: {', '.join(highlights[:3])}\n"
                    f"Has action: {transcript_analysis.get('has_action', False)}\n"
                    f"Has humor: {transcript_analysis.get('has_humor', False)}\n"
                    f"Visual excitement score: {vision_score}/10\n"
                    f"Text relevance score: {prefilter}/10\n\n"
                    "Consider: Will US gun/military fans find this exciting enough to share?\n"
                    "Reply ONLY with a number 1-10."
                ),
            }],
            max_tokens=5,
            temperature=0,
        )
        score_str = resp.choices[0].message.content.strip()
        score = float("".join(c for c in score_str if c.isdigit() or c == "."))
        return min(10.0, max(1.0, score))

    except Exception as e:
        log.error(f"Final scoring error: {e}")
        # Вычисляем вручную
        return (vision_score + prefilter + transcript_analysis.get("quality", 5)) / 3


# ─── Главная функция скоринга ─────────────────────────────────────────────────

def score_candidates(
    candidates: list[VideoCandidate],
    download_dir: str,
    published_embeddings: list[list[float]] = None,
) -> list[dict]:
    """
    Полный пайплайн скоринга.
    Возвращает список dict с полями: candidate, score, transcript, analysis
    """
    os.makedirs(download_dir, exist_ok=True)

    # Этап 1: быстрый текстовый фильтр
    candidates = prefilter_by_text(candidates)

    # Этап 2: дедупликация
    candidates = deduplicate_with_embeddings(candidates, published_embeddings)

    # Берём топ по предварительному скору для детального анализа
    candidates.sort(key=lambda c: c.raw.get("prefilter_score", 0), reverse=True)
    top_candidates = candidates[:12]  # экономим кредиты NVIDIA

    results = []
    for c in top_candidates:
        log.info(f"Оцениваем: {c.title[:60]}")
        video_path = os.path.join(download_dir, f"{c.id}.mp4")

        # Скачиваем для анализа
        downloaded = _download_for_analysis(c.url, video_path)

        # Транскрипция
        transcript = ""
        if downloaded:
            transcript = transcribe_audio(video_path)

        # Анализ транскрипта
        analysis = analyze_transcript(transcript, c.title)

        # Vision scoring (только для топ-8 по тексту)
        vision_score = 5.0
        if downloaded and top_candidates.index(c) < 8:
            vision_score = score_frames_with_qwen(video_path, c.title)

        # Финальный score
        score = final_score(c, analysis, vision_score)
        c.raw["final_score"] = score
        c.raw["vision_score"] = vision_score

        results.append({
            "candidate": c,
            "score": score,
            "transcript": transcript,
            "analysis": analysis,
            "video_path": video_path if downloaded else None,
        })

        log.info(f"Score={score:.1f} | '{c.title[:50]}'")
        time.sleep(0.5)

    # Сортируем по финальному score
    results.sort(key=lambda r: r["score"], reverse=True)
    log.info(f"Скоринг завершён. Топ: {results[0]['candidate'].title[:50]} ({results[0]['score']:.1f})" if results else "Нет результатов")

    return results


# ─── Хелперы ─────────────────────────────────────────────────────────────────

def _download_for_analysis(url: str, output_path: str, max_sec: int = 90) -> bool:
    """Скачиваем видео через yt-dlp (Python API) для анализа."""
    import yt_dlp
    try:
        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'match_filter': yt_dlp.utils.match_filter_func(f"duration <= {max_sec}")
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return os.path.exists(output_path)
    except Exception as e:
        log.error(f"Download error {url}: {e}")
        return False


def _extract_frames(video_path: str, count: int = 5) -> list[str]:
    """Извлекаем N кадров равномерно из видео."""
    frames = []
    try:
        # Получаем длительность
        probe = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path
        ], capture_output=True, text=True, timeout=15)
        info = json.loads(probe.stdout)
        duration = float(next(
            (s["duration"] for s in info.get("streams", []) if s.get("codec_type") == "video"),
            30
        ))

        step = duration / (count + 1)
        tmpdir = tempfile.mkdtemp()

        for i in range(count):
            ts = step * (i + 1)
            frame_path = os.path.join(tmpdir, f"frame_{i}.jpg")
            result = subprocess.run([
                "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                "-vframes", "1", "-q:v", "3",
                "-vf", "scale=640:-1",
                frame_path
            ], capture_output=True, timeout=15)
            if result.returncode == 0 and os.path.exists(frame_path):
                frames.append(frame_path)

    except Exception as e:
        log.error(f"Frame extraction error: {e}")

    return frames
