# 🔫 Gun Channel — Automated YouTube Shorts Pipeline

Полностью автоматический пайплайн для US military & gun culture канала.
**100% бесплатно** на GitHub Actions.

## Архитектура

```
Поиск (YouTube CC / DVIDS / Reddit / Rumble / Odysee / Telegram)
  ↓
AI Скоринг (Groq Llama 8B pre-filter → Gemini Embedding дедупл → Qwen3.5-397B vision → Groq 70B final)
  ↓
Скрипт (Gemini 3.1 Flash Lite 500RPD → Qwen3-235B fallback)
  ↓
TTS (Gemini 2.5 Flash TTS → Edge TTS fallback)
  ↓
Монтаж (FFmpeg: вертикальный 1080x1920 + субтитры + best moment)
  ↓
Upload (Google Drive backup → YouTube Shorts)
```

## Быстрый старт

### 1. Fork репозитория

### 2. Получи API ключи

| Секрет | Где получить |
|--------|-------------|
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| `NVIDIA_API_KEY` | [build.nvidia.com](https://build.nvidia.com) → Get API Key |
| `YOUTUBE_API_KEY` | Google Cloud Console → YouTube Data API v3 |
| `YOUTUBE_CLIENT_SECRET_JSON` | Google Cloud Console → OAuth 2.0 Client |
| `YOUTUBE_TOKEN_JSON` | Запусти `python utils/get_token.py` локально |
| `GDRIVE_FOLDER_ID` | ID папки в Google Drive (из URL) |
| `REDDIT_CLIENT_ID` | [reddit.com/prefs/apps](https://reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | Там же |

### 3. YouTube OAuth Token (одноразово, локально)

```bash
pip install google-auth-oauthlib
python utils/get_token.py client_secret.json
# Скопируй token.json → GitHub Secret YOUTUBE_TOKEN_JSON
```

### 4. Добавь секреты в GitHub

Settings → Secrets and variables → Actions → New repository secret

### 5. Запуск

Автоматически: Пн, Ср, Пт в 11:00 UTC

Вручную: Actions → Gun Channel Pipeline → Run workflow

## Лимиты (бесплатно)

| Сервис | Лимит | Использование |
|--------|-------|---------------|
| Gemini 3.1 Flash Lite | 500 RPD | Скрипт |
| Gemini TTS | 10 RPD | Озвучка финальных |
| Gemini Embedding 2 | 1K RPD | Дедупликация |
| Gemma 4 31B | 1.5K RPD, ∞ TPM | Анализ транскриптов |
| Groq Whisper | 2K RPD | Транскрипция |
| Groq Llama 3.3 70B | 1K RPD | Viral score |
| Groq Llama 3.1 8B | 14.4K RPD | Pre-filter |
| NVIDIA Qwen3.5-397B | 1K кредитов | Vision scoring |
| Edge TTS | ∞ | Fallback озвучка |
| GitHub Actions | ∞ (публичное репо) | Оркестрация |
| Google Drive | 15 GB | Хранилище |

## Конфигурация

Всё настраивается в `config.py`:
- `MAX_CANDIDATES_PER_RUN` — сколько видео искать (default: 20)
- `TOP_VIDEOS_PER_RUN` — сколько Shorts делать за запуск (default: 3)
- `MIN_SCORE_THRESHOLD` — порог viral score (default: 7.0)
- `CHANNEL_STYLE` — описание канала для AI
- `TTS_VOICE_PROMPT` — промпт стиля голоса
- `REDDIT_SUBREDDITS` — список сабреддитов
- `YOUTUBE_SEARCH_QUERIES` — поисковые запросы

## Структура проекта

```
gun_channel/
├── main.py                    # Оркестратор
├── config.py                  # Конфигурация
├── requirements.txt
├── pipeline/
│   ├── search.py              # Мультисорсный поиск
│   ├── score.py               # AI скоринг (Qwen vision + Groq + Gemini)
│   ├── script.py              # Генерация скрипта
│   ├── tts.py                 # Синтез речи
│   ├── edit.py                # FFmpeg монтаж
│   └── upload.py              # YouTube + Drive
├── utils/
│   └── get_token.py           # OAuth helper
└── .github/
    └── workflows/
        └── pipeline.yml       # GitHub Actions
```

## Добавление источников

В `pipeline/search.py` добавляй новые функции по шаблону.
В `config.py` добавляй ключи/настройки.
В `search_all()` подключай новую функцию.
