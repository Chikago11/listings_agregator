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

# --- Limits ---
MAX_SOURCE_LINKS = 5
SEND_DELAY_SEC = 0.05   # анти-flood при рассылке
