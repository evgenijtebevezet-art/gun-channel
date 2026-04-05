import os

# ─── API KEYS (из GitHub Secrets) ───────────────────────────────────────────
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
NVIDIA_API_KEY   = os.environ.get("NVIDIA_API_KEY", "")   # nvapi-...
YOUTUBE_API_KEY  = os.environ.get("YOUTUBE_API_KEY", "")

# YouTube upload OAuth (JSON строка из секрета)
YOUTUBE_CLIENT_SECRET_JSON = os.environ.get("YOUTUBE_CLIENT_SECRET_JSON", "")
YOUTUBE_TOKEN_JSON         = os.environ.get("YOUTUBE_TOKEN_JSON", "")

# Google Drive
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

# Reddit
REDDIT_CLIENT_ID     = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = "GunChannelBot/1.0"

# ─── МОДЕЛИ ─────────────────────────────────────────────────────────────────
GEMINI_SCRIPT_MODEL  = "gemini-3.1-flash-lite-preview"   # 500 RPD — основной
GEMINI_RESERVE_MODEL = "gemini-2.5-flash"                  # резерв
GEMINI_TTS_MODEL     = "gemini-2.5-flash-preview-tts"      # 10 RPD — только финал
GEMINI_EMBED_MODEL   = "models/text-embedding-004"

GROQ_LLM_MODEL    = "llama-3.3-70b-versatile"   # 1K RPD — viral score
GROQ_FAST_MODEL   = "llama-3.1-8b-instant"       # 14.4K RPD — pre-фильтр
GROQ_WHISPER_MODEL = "whisper-large-v3"           # 2K RPD — транскрипция

NVIDIA_VISION_MODEL = "qwen/qwen3.5-397b-a17b"   # мультимодаль — скоринг кадров
NVIDIA_TEXT_MODEL   = "qwen/qwen3-235b-a22b"      # текст — резерв скрипта

# Edge TTS (fallback, без лимитов)
EDGE_TTS_VOICE = "en-US-GuyNeural"  # глубокий мужской американский

# ─── КАНАЛ ───────────────────────────────────────────────────────────────────
CHANNEL_STYLE = (
    "American military and gun culture. "
    "Two sub-niches: (1) military/defense — tanks, weapons systems, special forces, "
    "war footage; (2) ranch and range — funny gun moments, 2A culture, shooting ranges. "
    "Audience: US adults 18-45, patriotic, pro-2A. "
    "Tone: confident, punchy, slightly dramatic. No political lectures."
)

CHANNEL_LANGUAGE = "en-US"

# Промпт для TTS голоса
TTS_VOICE_PROMPT = (
    "Deep American male narrator. Confident military cadence. "
    "Steady, authoritative pace. Slight excitement on action moments. "
    "Like a History Channel documentary host."
)

# ─── ИСТОЧНИКИ ПОИСКА ────────────────────────────────────────────────────────
REDDIT_SUBREDDITS = [
    "militarygfys", "guns", "ar15", "NFA", "longrange",
    "Military", "CombatFootage", "ukraineRussiaReport",
    "ranchers", "Firearms"
]

YOUTUBE_SEARCH_QUERIES = [
    "military weapons test footage",
    "tank battle real footage",
    "special forces training",
    "gun range fails funny",
    "shooting range compilation",
    "ranch shooting 2A",
    "military equipment demonstration",
    "sniper compilation",
]

TELEGRAM_CHANNELS = [
    # добавь свои gun/military каналы
    # "militarymemes", "gunschannel"
]

# ─── ПАЙПЛАЙН НАСТРОЙКИ ──────────────────────────────────────────────────────
MAX_CANDIDATES_PER_RUN  = 20   # сколько видео качаем для оценки
TOP_VIDEOS_PER_RUN      = 3    # сколько делаем в итоге
MIN_SCORE_THRESHOLD     = 7.0  # минимальный viral score (из 10)

VIDEO_MIN_DURATION_SEC  = 15
VIDEO_MAX_DURATION_SEC  = 180  # берём источники до 3 мин, режем в Shorts

SHORTS_DURATION_SEC     = 55   # итоговый Shorts (чуть меньше 60)
SHORTS_RESOLUTION       = "1080x1920"

SUBTITLE_FONT_SIZE      = 14
SUBTITLE_FONT_COLOR     = "white"
SUBTITLE_OUTLINE_COLOR  = "black"

WORK_DIR     = "/tmp/gun_channel"
DOWNLOAD_DIR = f"{WORK_DIR}/downloads"
OUTPUT_DIR   = f"{WORK_DIR}/output"
