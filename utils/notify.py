"""
utils/notify.py — Telegram уведомления о готовых видео.
Настройка:
  1. Создай бота через @BotFather → получи TELEGRAM_BOT_TOKEN
  2. Напиши боту /start, потом зайди:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     Найди "chat":{"id": XXXXXXXX} — это твой TELEGRAM_CHAT_ID
  3. Добавь оба значения в GitHub Secrets.
"""

import os
import json
import urllib.request
import urllib.parse
import logging

log = logging.getLogger(__name__)

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")


def _send(text: str) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.debug("Telegram не настроен — пропускаем уведомление")
        return False
    try:
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning(f"Telegram send error: {e}")
        return False


def notify_video_ready(title: str, yt_url: str | None, score: float, source: str, index: int, total: int):
    """Уведомление когда видео готово и загружено."""
    status = "✅ Загружено на YouTube" if yt_url else "💾 Сохранено локально (YouTube пропущен)"
    link = f'\n🔗 <a href="{yt_url}">{yt_url}</a>' if yt_url else ""
    msg = (
        f"🔫 <b>Gun Channel — Видео готово!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{title[:80]}</b>\n"
        f"📊 Score: <b>{score:.1f}/10</b> | Источник: {source}\n"
        f"📹 Ролик {index}/{total}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status}{link}"
    )
    return _send(msg)


def notify_pipeline_start(run_number: str = ""):
    """Уведомление о старте пайплайна."""
    msg = (
        f"🚀 <b>Gun Channel Pipeline запущен</b>\n"
        f"⏰ GitHub Actions Run {run_number}\n"
        f"📡 Ищем лучший военный/gun контент..."
    )
    return _send(msg)


def notify_pipeline_done(results: list):
    """Итоговое уведомление после завершения всего пайплайна."""
    ok = [r for r in results if r.get("success")]
    fail = [r for r in results if not r.get("success")]

    lines = []
    for r in ok:
        url = r.get("yt_url", "")
        if url and url.startswith("http"):
            lines.append(f'✅ <a href="{url}">{r["title"][:50]}</a>')
        else:
            lines.append(f'💾 {r["title"][:50]} (локально)')
    for r in fail:
        lines.append(f'❌ {r.get("title", "unknown")[:50]}')

    summary = "\n".join(lines) if lines else "Нет результатов"
    msg = (
        f"🏁 <b>Gun Channel — Пайплайн завершён</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Готово: {len(ok)} | Ошибок: {len(fail)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{summary}"
    )
    return _send(msg)


def notify_error(error_msg: str):
    """Уведомление об ошибке."""
    msg = (
        f"🚨 <b>Gun Channel — ОШИБКА</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<code>{str(error_msg)[:300]}</code>"
    )
    return _send(msg)
