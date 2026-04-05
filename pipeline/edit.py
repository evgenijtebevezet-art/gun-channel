"""
edit.py — монтаж Shorts
- Обрезка лучшего момента из видео
- Вертикальный формат 1080x1920
- Burn-in субтитры через Groq Whisper
- Наложение озвучки
- Финальный export
"""

import os
import json
import logging
import subprocess
import tempfile
from pathlib import Path

import config

log = logging.getLogger(__name__)


def make_short(
    video_path: str,
    audio_path: str,
    transcript_text: str,
    output_path: str,
    best_moment_start: float = 0,
) -> bool:
    """
    Полный монтаж Shorts.
    video_path    — исходное скачанное видео
    audio_path    — синтезированная озвучка (mp3)
    transcript_text — текст озвучки для субтитров
    output_path   — куда сохранить готовый Shorts
    best_moment_start — с какой секунды начать нарезку
    """
    if not os.path.exists(video_path):
        log.error(f"Видео не найдено: {video_path}")
        return False

    if not os.path.exists(audio_path):
        log.error(f"Аудио не найдено: {audio_path}")
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="shorts_edit_")

    try:
        # Получаем длительность озвучки
        audio_dur = _get_duration(audio_path)
        if audio_dur <= 0:
            log.error("Не удалось получить длительность аудио")
            return False

        target_dur = min(audio_dur + 1.0, config.SHORTS_DURATION_SEC)

        # Шаг 1: Обрезаем видео под нужную длину
        trimmed_video = os.path.join(tmpdir, "trimmed.mp4")
        ok = _trim_video(video_path, trimmed_video, best_moment_start, target_dur)
        if not ok:
            return False

        # Шаг 2: Конвертируем в вертикальный формат 1080x1920
        vertical_video = os.path.join(tmpdir, "vertical.mp4")
        ok = _make_vertical(trimmed_video, vertical_video)
        if not ok:
            return False

        # Шаг 3: Генерируем субтитры
        srt_path = os.path.join(tmpdir, "subs.srt")
        _generate_srt(transcript_text, audio_dur, srt_path)

        # Шаг 4: Финальная сборка — видео + озвучка + субтитры
        ok = _final_compose(vertical_video, audio_path, srt_path, output_path, target_dur)
        if not ok:
            return False

        log.info(f"Shorts готов: {output_path}")
        return True

    except Exception as e:
        log.error(f"Edit error: {e}")
        return False
    finally:
        # Чистим временные файлы
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def _trim_video(input_path: str, output_path: str, start: float, duration: float) -> bool:
    """Обрезаем видео начиная с best_moment."""
    video_dur = _get_duration(input_path)

    # Если видео короче нужного — начинаем с 0
    if start + duration > video_dur:
        start = max(0, video_dur - duration)

    result = subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-c:a", "aac",
        "-avoid_negative_ts", "make_zero",
        output_path
    ], capture_output=True, timeout=120)

    if result.returncode != 0:
        log.error(f"Trim error: {result.stderr.decode()[-500:]}")
        return False
    return True


def _make_vertical(input_path: str, output_path: str) -> bool:
    """
    Конвертируем в 1080x1920 (вертикальный формат Shorts).
    Стратегия: crop центр + scale, или blur боковые полосы если видео горизонтальное.
    """
    # Получаем размеры исходного видео
    probe = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", input_path
    ], capture_output=True, text=True, timeout=15)

    try:
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        w = int(stream.get("width", 1920))
        h = int(stream.get("height", 1080))
    except Exception:
        w, h = 1920, 1080

    aspect = w / h if h > 0 else 1.78

    if aspect > 1:
        # Горизонтальное видео → blur background + centered crop
        vf = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:20[blurred];"
            "[fg]scale=-1:1920:force_original_aspect_ratio=decrease[scaled];"
            "[blurred][scaled]overlay=(W-w)/2:(H-h)/2[out]"
        )
    else:
        # Вертикальное / квадратное видео → просто масштабируем
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

    result = subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", vf if aspect > 1 else "",
        "-map", "[out]" if aspect > 1 else "0:v",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac",
        "-s", "1080x1920",
        output_path
    ], capture_output=True, timeout=180)

    if result.returncode != 0:
        # Fallback: простое масштабирование
        result = subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac",
            output_path
        ], capture_output=True, timeout=180)

    if result.returncode != 0:
        log.error(f"Vertical convert error: {result.stderr.decode()[-300:]}")
        return False

    return True


def _generate_srt(text: str, total_duration: float, srt_path: str):
    """
    Генерируем SRT файл — простое равномерное распределение слов по времени.
    Для точных субтитров используй Whisper транскрипцию озвучки.
    """
    words = text.split()
    if not words:
        open(srt_path, "w").close()
        return

    # Группируем по ~4 слова на строку
    groups = []
    chunk = []
    for w in words:
        chunk.append(w)
        if len(chunk) >= 4:
            groups.append(" ".join(chunk))
            chunk = []
    if chunk:
        groups.append(" ".join(chunk))

    if not groups:
        open(srt_path, "w").close()
        return

    time_per_group = total_duration / len(groups)

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, group in enumerate(groups):
            start = i * time_per_group
            end = (i + 1) * time_per_group
            f.write(f"{i + 1}\n")
            f.write(f"{_fmt_time(start)} --> {_fmt_time(end)}\n")
            f.write(f"{group.upper()}\n\n")


def _final_compose(
    video_path: str,
    audio_path: str,
    srt_path: str,
    output_path: str,
    duration: float,
) -> bool:
    """Финальная сборка: видео без звука + новая озвучка + субтитры."""

    subtitle_filter = (
        f"subtitles={srt_path}:force_style='"
        f"Fontsize={config.SUBTITLE_FONT_SIZE},"
        f"FontName=Arial,"
        f"Bold=1,"
        f"PrimaryColour=&H00FFFFFF,"       # белый текст
        f"OutlineColour=&H00000000,"       # чёрная обводка
        f"BackColour=&H80000000,"          # полупрозрачный фон
        f"Outline=2,"
        f"Shadow=1,"
        f"Alignment=2,"                    # по центру снизу
        f"MarginV=120'"                    # отступ снизу (не перекрывает UI)
    )

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-t", str(duration),
        "-map", "0:v:0",   # видео из источника
        "-map", "1:a:0",   # аудио из озвучки
        "-vf", subtitle_filter,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",         # для быстрого стриминга
        "-shortest",
        output_path
    ], capture_output=True, timeout=300)

    if result.returncode != 0:
        log.error(f"Final compose error: {result.stderr.decode()[-500:]}")
        # Пробуем без субтитров
        return _compose_without_subs(video_path, audio_path, output_path, duration)

    return True


def _compose_without_subs(video_path, audio_path, output_path, duration) -> bool:
    """Fallback без субтитров."""
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path, "-i", audio_path,
        "-t", str(duration),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest",
        output_path
    ], capture_output=True, timeout=300)
    return result.returncode == 0


def _get_duration(path: str) -> float:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", path
        ], capture_output=True, text=True, timeout=15)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _fmt_time(seconds: float) -> str:
    """Форматируем время в SRT формат HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def find_best_moment(video_path: str, duration: float = 55) -> float:
    """
    Простая эвристика: ищем момент с наибольшим motion (разница кадров).
    Возвращает timestamp начала лучшего момента.
    """
    try:
        video_dur = _get_duration(video_path)
        if video_dur <= duration:
            return 0

        # Анализируем motion через ffmpeg
        result = subprocess.run([
            "ffmpeg", "-i", video_path,
            "-vf", "select='gt(scene,0.3)',showinfo",
            "-vsync", "vfr", "-f", "null", "-"
        ], capture_output=True, text=True, timeout=60)

        # Парсим timestamps сцен
        scene_times = []
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    ts = float(line.split("pts_time:")[1].split()[0])
                    scene_times.append(ts)
                except Exception:
                    pass

        if not scene_times:
            return 0

        # Находим окно с наибольшим кол-вом сцен (= больше всего action)
        best_start = 0
        best_count = 0
        for ts in scene_times:
            count = sum(1 for t in scene_times if ts <= t <= ts + duration)
            if count > best_count:
                best_count = count
                best_start = ts

        # Убеждаемся что нарезка не выходит за конец
        best_start = min(best_start, video_dur - duration)
        return max(0, best_start)

    except Exception as e:
        log.error(f"Best moment detection error: {e}")
        return 0
