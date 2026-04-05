"""
script.py — генерация скрипта для Shorts
Основная модель: Gemini 3.1 Flash Lite (500 RPD)
Резерв: Qwen3-235B (NVIDIA NIM)
"""

import re
import json
import time
import logging
import requests

import config
from openai import OpenAI

log = logging.getLogger(__name__)

nvidia_client = OpenAI(
    api_key=config.NVIDIA_API_KEY,
    base_url="https://integrate.api.nvidia.com/v1",
)

SYSTEM_PROMPT = f"""You are a top-tier scriptwriter for a viral US military and firearms YouTube Shorts channel. 
Your goal is 100% viewer retention.

Channel style: {config.CHANNEL_STYLE}

Rules for the script:
1. THE HOOK: The first 3 seconds must be explosive, curious, or controversial. Never start with "In this video." Start with action: "This is why the military prefers X...", "Never do this at the range...", "POV: You're cornered."
2. EXPERT TONE: Use proper military/firearm terminology. Sound like a seasoned veteran, instructor, or tactical expert. Do NOT sound formal or like AI. Be confident, witty, and grounded.
3. PACING: Short, punchy, high-energy sentences. Max 8-10 words per sentence. Build tension sequentially.
4. NO FLUFF: Cut boring introductions. Jump straight to the point.
5. CLEAN TEXT: NO EMOJIS, NO stage directions, NO hashtags in the body. Write EXACTLY what the narrator will speak.
6. LENGTH: Exactly 100-130 words (fits 45-55 seconds of fast-paced speech).
7. CALL TO ACTION: End with a sharp, engaging question or statement: "What's your weapon of choice? Let me know below." or "Subscribe for daily tactical content."
8. NEUTRALITY: NO political debates. Focus purely on the raw power, science, humor, or mechanics of the situation.
"""


def generate_script(
    title: str,
    transcript: str,
    analysis: dict,
    duration_sec: int = 0,
) -> dict:
    """
    Генерирует скрипт для Shorts.
    Возвращает dict: {hook, body, cta, full_text, title_suggestion, hashtags}
    """
    context = _build_context(title, transcript, analysis)

    # Пробуем Gemini 3.1 Flash Lite
    result = _generate_with_gemini(context)
    if result:
        return result

    # Резерв: Qwen3-235B
    log.warning("Gemini недоступен, пробуем Qwen3-235B")
    result = _generate_with_qwen(context)
    if result:
        return result

    # Крайний резерв: простой шаблон
    return _fallback_script(title, analysis)


def _build_context(title: str, transcript: str, analysis: dict) -> str:
    summary = analysis.get("summary", "")
    highlights = analysis.get("highlights", [])
    has_action = analysis.get("has_action", False)
    has_humor = analysis.get("has_humor", False)

    context = f"Video title: {title}\n"
    if summary:
        context += f"Content: {summary}\n"
    if highlights:
        context += f"Key moments: {', '.join(highlights[:3])}\n"
    if has_action:
        context += "Style note: This has intense action — lean into it!\n"
    if has_humor:
        context += "Style note: There are funny moments — add some wit!\n"
    if transcript:
        context += f"\nOriginal transcript excerpt:\n{transcript[:800]}\n"

    return context


def _generate_with_gemini(context: str) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None

    prompt = (
        f"{context}\n\n"
        "Write a YouTube Shorts script for this video. "
        "Return JSON only:\n"
        '{"hook": "first 3 seconds text", '
        '"body": "main narration text", '
        '"cta": "call to action", '
        '"full_text": "complete script hook+body+cta", '
        '"title_suggestion": "catchy YouTube title under 60 chars", '
        '"hashtags": ["tag1", "tag2", "tag3", "tag4", "tag5"]}'
    )

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_SCRIPT_MODEL}:generateContent",
            params={"key": config.GEMINI_API_KEY},
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.8,
                    "maxOutputTokens": 600,
                    "responseMimeType": "application/json",
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = _parse_json_response(text)
        if result:
            result["model_used"] = config.GEMINI_SCRIPT_MODEL
            log.info(f"Скрипт сгенерирован: {config.GEMINI_SCRIPT_MODEL}")
            return result

    except Exception as e:
        log.error(f"Gemini script error: {e}")

    return None


def _generate_with_qwen(context: str) -> dict | None:
    if not config.NVIDIA_API_KEY:
        return None

    prompt = (
        f"{context}\n\n"
        "Write a YouTube Shorts script. "
        "Return JSON only (no markdown, no backticks):\n"
        '{"hook": "...", "body": "...", "cta": "...", '
        '"full_text": "...", "title_suggestion": "...", '
        '"hashtags": ["tag1", "tag2", "tag3", "tag4", "tag5"]}'
    )

    try:
        resp = nvidia_client.chat.completions.create(
            model=config.NVIDIA_TEXT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
            temperature=0.8,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        text = resp.choices[0].message.content
        result = _parse_json_response(text)
        if result:
            result["model_used"] = config.NVIDIA_TEXT_MODEL
            log.info(f"Скрипт сгенерирован: {config.NVIDIA_TEXT_MODEL}")
            return result

    except Exception as e:
        log.error(f"Qwen script error: {e}")

    return None


def _fallback_script(title: str, analysis: dict) -> dict:
    """Минимальный шаблон если все API недоступны."""
    hook = f"You won't believe what happened here."
    body = f"{title}. This is the kind of content real patriots live for."
    cta = "Follow for daily military and gun content!"
    return {
        "hook": hook,
        "body": body,
        "cta": cta,
        "full_text": f"{hook} {body} {cta}",
        "title_suggestion": title[:60],
        "hashtags": ["military", "guns", "2A", "firearms", "USA"],
        "model_used": "fallback",
    }


def _parse_json_response(text: str) -> dict | None:
    try:
        # Убираем markdown-обёртку если есть
        text = re.sub(r'^```json\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        data = json.loads(text)
        required = {"hook", "body", "cta", "full_text", "title_suggestion", "hashtags"}
        if required.issubset(data.keys()):
            return data
    except Exception:
        # Пробуем вытащить JSON регулярками
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def generate_description(script: dict, channel_style: str = None) -> str:
    """Генерирует описание для YouTube."""
    full_text = script.get("full_text", "")
    hashtags = " ".join(f"#{h.replace(' ', '')}" for h in script.get("hashtags", []))

    description = (
        f"{full_text}\n\n"
        f"🔔 Subscribe for daily military & gun content!\n"
        f"👇 Drop a comment below!\n\n"
        f"{hashtags}\n\n"
        f"#military #guns #2A #firearms #USA #shorts"
    )
    return description[:5000]
