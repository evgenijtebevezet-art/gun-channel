"""
test_pipeline.py — тест пайплайна без загрузки на YouTube.
Запускай локально чтобы проверить каждый модуль.

Использование:
  python test_pipeline.py                    # полный тест
  python test_pipeline.py --step search     # только поиск
  python test_pipeline.py --step score      # поиск + скоринг
  python test_pipeline.py --step script     # до генерации скрипта
  python test_pipeline.py --step tts        # до TTS
  python test_pipeline.py --step edit       # до монтажа
"""

import os
import sys
import json
import argparse
import logging
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("test")

sys.path.insert(0, str(Path(__file__).parent))
import config

# Тест-режим: уменьшаем лимиты
config.MAX_CANDIDATES_PER_RUN = 5
config.TOP_VIDEOS_PER_RUN = 1
config.MIN_SCORE_THRESHOLD = 3.0  # низкий порог для теста

STEPS = ["search", "score", "script", "tts", "edit", "all"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=STEPS, default="all")
    parser.add_argument("--url", help="Конкретный URL для теста (пропускает поиск)")
    args = parser.parse_args()

    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"🧪 Gun Channel Pipeline Test — step: {args.step}")
    print("=" * 60 + "\n")

    # ── Поиск ───────────────────────────────────────────────────────────────
    if args.url:
        from pipeline.search import VideoCandidate
        candidates = [VideoCandidate(
            id="test_manual",
            source="manual",
            url=args.url,
            title="Manual test video",
        )]
        print(f"✅ Ручной URL: {args.url}\n")
    else:
        print("📡 Тест поиска...")
        from pipeline.search import search_all
        candidates = search_all(max_total=config.MAX_CANDIDATES_PER_RUN)
        print(f"\n✅ Найдено кандидатов: {len(candidates)}")
        for c in candidates:
            print(f"   [{c.source:10}] {c.title[:55]:<55} {c.duration_sec}s")
        print()

        if not candidates:
            print("❌ Нет кандидатов — проверь API ключи")
            sys.exit(1)

    if args.step == "search":
        _save_json(candidates, "test_search_results.json")
        return

    # ── Скоринг ─────────────────────────────────────────────────────────────
    print("🤖 Тест скоринга (это займёт 1-2 минуты)...")
    from pipeline.score import score_candidates
    scored = score_candidates(
        candidates,
        download_dir=config.DOWNLOAD_DIR,
        published_embeddings=[],
    )

    print(f"\n✅ Оценено: {len(scored)} видео")
    for r in scored:
        c = r["candidate"]
        print(f"   Score={r['score']:.1f} | [{c.source}] {c.title[:50]}")
        if r.get("analysis", {}).get("summary"):
            print(f"           → {r['analysis']['summary'][:80]}")
    print()

    if not scored:
        print("❌ Нет результатов скоринга")
        sys.exit(1)

    if args.step == "score":
        _save_json([{
            "title": r["candidate"].title,
            "score": r["score"],
            "analysis": r["analysis"],
        } for r in scored], "test_score_results.json")
        return

    # Берём лучший для дальнейших тестов
    best = scored[0]
    candidate = best["candidate"]
    transcript = best["transcript"]
    analysis = best["analysis"]
    video_path = best.get("video_path")

    # ── Скрипт ──────────────────────────────────────────────────────────────
    print(f"📝 Тест генерации скрипта для: {candidate.title[:50]}")
    from pipeline.script import generate_script, generate_description
    script = generate_script(
        title=candidate.title,
        transcript=transcript,
        analysis=analysis,
    )

    print(f"\n✅ Скрипт сгенерирован ({script.get('model_used', '?')})")
    print(f"   Заголовок : {script['title_suggestion']}")
    print(f"   Хук       : {script['hook']}")
    print(f"   Основная  : {script['body'][:80]}...")
    print(f"   CTA       : {script['cta']}")
    print(f"   Хэштеги   : {' '.join('#' + h for h in script['hashtags'][:5])}")
    print(f"   Длина     : {len(script['full_text'].split())} слов")
    print()

    if args.step == "script":
        _save_json(script, "test_script_result.json")
        return

    # ── TTS ─────────────────────────────────────────────────────────────────
    print("🎙️ Тест синтеза речи (Edge TTS, без лимитов)...")
    audio_path = os.path.join(config.OUTPUT_DIR, "test_audio.mp3")

    from pipeline.tts import synthesize, get_audio_duration
    # В тесте всегда используем Edge TTS (экономим Gemini TTS лимит)
    ok = synthesize(script["full_text"], audio_path, force_edge=True)

    if ok:
        dur = get_audio_duration(audio_path)
        print(f"✅ Аудио: {audio_path} ({dur:.1f}s)\n")
    else:
        print("❌ TTS не удался\n")
        if args.step == "tts":
            return

    if args.step == "tts":
        return

    # ── Монтаж ──────────────────────────────────────────────────────────────
    if not video_path or not os.path.exists(video_path):
        print("📥 Скачиваем видео для теста монтажа...")
        from pipeline.score import _download_for_analysis
        video_path = os.path.join(config.DOWNLOAD_DIR, f"{candidate.id}.mp4")
        downloaded = _download_for_analysis(candidate.url, video_path)
        if not downloaded:
            print("❌ Не удалось скачать видео для монтажа")
            return
        print(f"✅ Скачано: {video_path}\n")

    print("✂️ Тест монтажа...")
    output_path = os.path.join(config.OUTPUT_DIR, "test_short.mp4")

    from pipeline.edit import make_short, find_best_moment
    best_start = find_best_moment(video_path)
    print(f"   Лучший момент: {best_start:.1f}s")

    ok = make_short(
        video_path=video_path,
        audio_path=audio_path,
        transcript_text=script["full_text"],
        output_path=output_path,
        best_moment_start=best_start,
    )

    if ok:
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"✅ Shorts готов: {output_path} ({size_mb:.1f} MB)")
    else:
        print("❌ Монтаж не удался")

    if args.step == "edit":
        return

    # ── Итог ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ Все тесты пройдены!")
    print(f"   Видео: {output_path}")
    print(f"   Заголовок: {script['title_suggestion']}")
    print()
    print("Для загрузки на YouTube запусти:")
    print("  python main.py")
    print("=" * 60 + "\n")


def _save_json(data, filename: str):
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, "w") as f:
        if hasattr(data, '__iter__') and not isinstance(data, dict):
            items = []
            for item in data:
                if hasattr(item, 'to_dict'):
                    items.append(item.to_dict())
                else:
                    items.append(item)
            json.dump(items, f, indent=2, ensure_ascii=False)
        else:
            json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Результат сохранён: {path}")


if __name__ == "__main__":
    main()
