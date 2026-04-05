"""
tts.py — синтез речи
Основной: Gemini 2.5 Flash TTS (10 RPD — только финальные ролики)
Резерв:   Edge TTS en-US-GuyNeural (без лимитов)
"""

import os
import asyncio
import logging
import base64
import subprocess
import requests

import config

log = logging.getLogger(__name__)


def synthesize(text: str, output_path: str, force_edge: bool = False) -> bool:
    """
    Синтезирует речь из текста.
    force_edge=True — использовать Edge TTS (черновики, субтитры).
    Возвращает True если успешно.
    """
    # Gemini TTS — только для финальных роликов
    if not force_edge and config.GEMINI_API_KEY:
        success = _synthesize_gemini(text, output_path)
        if success:
            return True
        log.warning("Gemini TTS недоступен или лимит исчерпан, переключаемся на Edge TTS")

    # Edge TTS fallback
    return _synthesize_edge(text, output_path)


def _synthesize_gemini(text: str, output_path: str) -> bool:
    """Gemini 2.5 Flash TTS — лучшее качество голоса."""
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_TTS_MODEL}:generateContent",
            params={"key": config.GEMINI_API_KEY},
            json={
                "contents": [{
                    "parts": [{"text": text}],
                    "role": "user",
                }],
                # Gemini API использует camelCase — НЕ snake_case!
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Charon",  # глубокий мужской голос
                            }
                        },
                    },
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        # Извлекаем аудио данные
        audio_b64 = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("inlineData", {})
            .get("data", "")
        )

        if not audio_b64:
            log.error("Gemini TTS: пустой ответ")
            return False

        # Декодируем и сохраняем как WAV
        raw_wav = base64.b64decode(audio_b64)
        wav_path = output_path.replace(".mp3", ".wav")

        # Добавляем WAV-заголовок (PCM 16bit 24kHz mono)
        _write_wav(wav_path, raw_wav, sample_rate=24000)

        # Конвертируем в mp3
        if output_path.endswith(".mp3"):
            result = subprocess.run([
                "ffmpeg", "-y", "-i", wav_path,
                "-ar", "44100", "-b:a", "192k",
                output_path
            ], capture_output=True, timeout=30)
            os.remove(wav_path)
            if result.returncode != 0:
                return False
        else:
            os.rename(wav_path, output_path)

        log.info(f"Gemini TTS: сохранено в {output_path}")
        return True

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            log.warning("Gemini TTS: лимит запросов исчерпан (10/day)")
        else:
            log.error(f"Gemini TTS HTTP error: {e}")
        return False
    except Exception as e:
        log.error(f"Gemini TTS error: {e}")
        return False


def _synthesize_edge(text: str, output_path: str) -> bool:
    """Edge TTS — бесплатно, без лимитов. Голос GuyNeural."""
    try:
        # edge-tts устанавливается через pip
        result = subprocess.run([
            "edge-tts",
            "--voice", config.EDGE_TTS_VOICE,
            "--text", text,
            "--write-media", output_path,
            "--rate=+5%",   # чуть быстрее для энергичности
            "--pitch=-10Hz", # чуть ниже для авторитетности
        ], capture_output=True, timeout=60, text=True)

        if result.returncode == 0 and os.path.exists(output_path):
            log.info(f"Edge TTS: сохранено в {output_path}")
            return True
        else:
            log.error(f"Edge TTS error: {result.stderr}")
            return False

    except FileNotFoundError:
        # Пробуем через Python API
        return _synthesize_edge_python(text, output_path)
    except Exception as e:
        log.error(f"Edge TTS error: {e}")
        return False


def _synthesize_edge_python(text: str, output_path: str) -> bool:
    """Edge TTS через Python библиотеку."""
    try:
        import edge_tts  # noqa

        async def _run():
            communicate = edge_tts.Communicate(
                text,
                voice=config.EDGE_TTS_VOICE,
                rate="+5%",
                pitch="-10Hz",
            )
            await communicate.save(output_path)

        asyncio.run(_run())
        log.info(f"Edge TTS (python): сохранено в {output_path}")
        return True
    except Exception as e:
        log.error(f"Edge TTS Python error: {e}")
        return False


def _write_wav(path: str, pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16):
    """Записывает PCM данные в WAV файл с правильным заголовком."""
    import struct
    data_size = len(pcm_data)
    with open(path, "wb") as f:
        # RIFF заголовок
        f.write(b"RIFF")
        f.write(struct.pack("<I", data_size + 36))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))           # chunk size
        f.write(struct.pack("<H", 1))            # PCM format
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * channels * bits // 8))  # byte rate
        f.write(struct.pack("<H", channels * bits // 8))                # block align
        f.write(struct.pack("<H", bits))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(pcm_data)


def get_audio_duration(audio_path: str) -> float:
    """Возвращает длительность аудиофайла в секундах."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-show_entries",
            "format=duration", "-of", "csv=p=0", audio_path
        ], capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except Exception:
        return 0.0
