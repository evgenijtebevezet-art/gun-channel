"""
main.py — главный оркестратор пайплайна
Запускается из GitHub Actions по расписанию.
"""

import os
import sys
import json
import logging
import time
from pathlib import Path

import config
from pipeline.search import search_all
from pipeline.sources.telegram import search_telegram_public
from pipeline.score import score_candidates
from pipeline.script import generate_script, generate_description
from pipeline.tts import synthesize, get_audio_duration
from pipeline.edit import make_short, find_best_moment
from pipeline.upload import upload_to_youtube, upload_to_drive, _get_google_token
from utils.history import load_history, add_record, get_published_embeddings, get_published_urls
from utils.notify import notify_pipeline_start, notify_pipeline_done, notify_video_ready, notify_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


def run_pipeline():
    log.info("=" * 60)
    log.info("Gun Channel Pipeline запущен")
    log.info("=" * 60)
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "local")
    notify_pipeline_start(run_number)

    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    results_summary = []

    try:
        # ── История опубликованных ───────────────────────────────────────────
        google_token = _get_google_token()
        history = load_history(
            google_token=google_token,
            drive_folder_id=config.GDRIVE_FOLDER_ID,
        )
        published_urls = get_published_urls(history)
        published_embeddings = get_published_embeddings(history)
        log.info(f"История: {len(history)} опубликованных видео")

        # ── ШАГ 1: Поиск ────────────────────────────────────────────────────
        log.info("📡 Шаг 1: Поиск видео из всех источников...")
        candidates = search_all(max_total=config.MAX_CANDIDATES_PER_RUN)

        if config.TELEGRAM_CHANNELS:
            tg = search_telegram_public(config.TELEGRAM_CHANNELS, max_per_channel=5)
            candidates += tg
            log.info(f"Telegram добавил: {len(tg)} кандидатов")

        # Убираем уже опубликованные
        before = len(candidates)
        candidates = [c for c in candidates if c.url not in published_urls]
        if before != len(candidates):
            log.info(f"Отфильтровано уже опубликованных: {before - len(candidates)}")

        if not candidates:
            log.error("Нет новых кандидатов. Выход.")
            return

        log.info(f"Новых кандидатов: {len(candidates)}")

        # ── ШАГ 2: AI скоринг ───────────────────────────────────────────────
        log.info("🤖 Шаг 2: AI скоринг...")
        scored = score_candidates(
            candidates,
            download_dir=config.DOWNLOAD_DIR,
            published_embeddings=published_embeddings,
        )

        if not scored:
            log.error("После скоринга нет кандидатов.")
            return

        top = [r for r in scored if r["score"] >= config.MIN_SCORE_THRESHOLD]
        if not top:
            log.warning(f"Ниже порога {config.MIN_SCORE_THRESHOLD}. Берём топ-1.")
            top = scored[:1]
        top = top[:config.TOP_VIDEOS_PER_RUN]
        log.info(f"Отобрано для производства: {len(top)}")

        # ── ШАГ 3–7: Производство ───────────────────────────────────────────
        for i, result in enumerate(top, 1):
            candidate = result["candidate"]
            log.info(f"\n{'─' * 50}")
            log.info(f"🎬 Ролик {i}/{len(top)}: {candidate.title[:60]}")
            log.info(f"   Score: {result['score']:.1f} | Source: {candidate.source}")

            success = _produce_one(
                candidate=candidate,
                video_path=result.get("video_path"),
                transcript=result["transcript"],
                analysis=result["analysis"],
                index=i,
                results_summary=results_summary,
                google_token=google_token,
            )
            if not success:
                log.error(f"Ролик {i} провалился, продолжаем...")
            time.sleep(2)

    except Exception as e:
        log.exception(f"Критическая ошибка: {e}")
        notify_error(str(e))
        sys.exit(1)

    # ── Итог ────────────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("Pipeline done")
    for r in results_summary:
        s = "OK" if r["success"] else "FAIL"
        log.info(f"  {s} {r['title'][:50]} -> {r.get('yt_url', 'local')}")
    log.info("=" * 60)
    notify_pipeline_done(results_summary)


def _produce_one(
    candidate, video_path, transcript, analysis,
    index, results_summary, google_token=None,
) -> bool:
    fail = lambda: results_summary.append(
        {"success": False, "title": "unknown", "yt_url": None}
    ) or False

    # Шаг 3: Скрипт
    log.info("📝 Шаг 3: Генерация скрипта...")
    script = generate_script(
        title=candidate.title,
        transcript=transcript,
        analysis=analysis,
        duration_sec=candidate.duration_sec,
    )
    log.info(f"  [{script.get('model_used','?')}] {script['title_suggestion']}")
    log.info(f"  Hook: {script['hook']}")

    # Шаг 4: TTS
    log.info("🎙️ Шаг 4: Синтез речи...")
    audio_path = os.path.join(config.OUTPUT_DIR, f"audio_{index}.mp3")
    is_final = index <= 2
    tts_ok = synthesize(script["full_text"], audio_path, force_edge=not is_final)
    if not tts_ok:
        log.error("TTS не удался")
        results_summary.append({"success": False, "title": script["title_suggestion"], "yt_url": None})
        return False
    audio_dur = get_audio_duration(audio_path)
    log.info(f"  Аудио: {audio_dur:.1f}s")

    # Шаг 5: Скачивание (если нет)
    if not video_path or not os.path.exists(video_path):
        log.info("📥 Скачиваем видео...")
        from pipeline.score import _download_for_analysis
        video_path = os.path.join(config.DOWNLOAD_DIR, f"{candidate.id}.mp4")
        if not _download_for_analysis(candidate.url, video_path):
            log.error("Скачивание провалилось")
            results_summary.append({"success": False, "title": script["title_suggestion"], "yt_url": None})
            return False

    # Шаг 6: Монтаж
    log.info("✂️ Шаг 6: Монтаж...")
    output_path = os.path.join(config.OUTPUT_DIR, f"short_{index}.mp4")
    best_start = find_best_moment(video_path, duration=audio_dur + 2)
    log.info(f"  Best moment: {best_start:.1f}s")

    edit_ok = make_short(
        video_path=video_path,
        audio_path=audio_path,
        transcript_text=script["full_text"],
        output_path=output_path,
        best_moment_start=best_start,
    )
    if not edit_ok:
        log.error("Монтаж провалился")
        results_summary.append({"success": False, "title": script["title_suggestion"], "yt_url": None})
        return False
    log.info(f"  Shorts: {output_path}")

    # Шаг 7: Загрузка
    log.info("📤 Шаг 7: Загрузка...")
    description = generate_description(script)
    tags = list(set(script.get("hashtags", []) + ["shorts", "military", "guns", "2A", "USA"]))

    if config.GDRIVE_FOLDER_ID:
        drive_id = upload_to_drive(output_path, f"short_{index}_{candidate.id}.mp4")
        if drive_id:
            log.info(f"  Drive: {drive_id}")

    video_id = upload_to_youtube(
        video_path=output_path,
        title=script["title_suggestion"],
        description=description,
        tags=tags[:15],
    )

    yt_url = f"https://www.youtube.com/shorts/{video_id}" if video_id else None
    if yt_url:
        log.info(f"  YouTube: {yt_url}")
        add_record(
            video_id=video_id,
            title=script["title_suggestion"],
            source_url=candidate.url,
            embedding=candidate.raw.get("embedding"),
            google_token=google_token,
            drive_folder_id=config.GDRIVE_FOLDER_ID,
        )

    # Telegram уведомление о готовом ролике
    notify_video_ready(
        title=script["title_suggestion"],
        yt_url=yt_url,
        score=result["score"] if "score" in dir() else 0.0,
        source=candidate.source,
        index=index,
        total=len(results_summary) + 1,
    )

    results_summary.append({
        "success": True,
        "title": script["title_suggestion"],
        "yt_url": yt_url or f"local:{output_path}",
    })
    return True


if __name__ == "__main__":
    run_pipeline()
