import os
from dotenv import load_dotenv

load_dotenv()

# --- Telethon ---
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_NAME = os.environ.get("SESSION_NAME", "userbot_session")

# --- Bot API ---
BOT_TOKEN = os.environ["BOT_TOKEN"]

# --- Dedup ---
DEDUP_TEXT_TTL_SEC = int(os.environ.get("DEDUP_TEXT_TTL_SEC", "10800"))
DEDUP_STRUCT_TTL_SEC = int(os.environ.get("DEDUP_STRUCT_TTL_SEC", "43200"))
# Delistings are low-frequency and should not repeat within a short window.
DELISTING_STRUCT_TTL_SEC = int(
    os.environ.get("DELISTING_STRUCT_TTL_SEC", "604800")
)

# --- Edited messages ---
# Ignore edits of very old source posts to avoid duplicate alerts when channels
# bump/refresh historical messages.
MAX_EDIT_MESSAGE_AGE_SEC = int(os.environ.get("MAX_EDIT_MESSAGE_AGE_SEC", "3600"))
OLD_EDIT_BYPASS_CHANNELS = {
    item.strip().lstrip("@").lower()
    for item in os.environ.get("OLD_EDIT_BYPASS_CHANNELS", "newlistingsfeed").split(",")
    if item.strip()
}

# --- Message identity dedup ---
MESSAGE_SEEN_TTL_SEC = int(os.environ.get("MESSAGE_SEEN_TTL_SEC", "604800"))

# --- Backfill ---
BACKFILL_CHANNELS = [
    item.strip().lstrip("@")
    for item in os.environ.get("BACKFILL_CHANNELS", "newlistingsfeed").split(",")
    if item.strip()
]
BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "80"))
BACKFILL_INTERVAL_SEC = int(os.environ.get("BACKFILL_INTERVAL_SEC", "120"))
BACKFILL_MAX_AGE_SEC = int(os.environ.get("BACKFILL_MAX_AGE_SEC", "172800"))

# --- Limits ---
MAX_SOURCE_LINKS = 5
SEND_DELAY_SEC = 0.05

# --- Logging ---
POSTS_LOG_PATH = os.environ.get("POSTS_LOG_PATH", "posts_log.csv")

# --- Token history retention ---
TOKEN_TTL_DAYS = int(os.environ.get("TOKEN_TTL_DAYS", "14"))
