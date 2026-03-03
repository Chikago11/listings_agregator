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

# --- Edited messages ---
# Ignore edits of very old source posts to avoid duplicate alerts when channels
# bump/refresh historical messages.
MAX_EDIT_MESSAGE_AGE_SEC = int(os.environ.get("MAX_EDIT_MESSAGE_AGE_SEC", "3600"))
OLD_EDIT_BYPASS_CHANNELS = {
    item.strip().lstrip("@").lower()
    for item in os.environ.get("OLD_EDIT_BYPASS_CHANNELS", "newlistingsfeed").split(",")
    if item.strip()
}

# --- Limits ---
MAX_SOURCE_LINKS = 5
SEND_DELAY_SEC = 0.05

# --- Logging ---
POSTS_LOG_PATH = os.environ.get("POSTS_LOG_PATH", "posts_log.csv")
