CHANNELS = [
    "metascalp_announcements_ru",
    "listing_alarm",
    "rickler_alerts",
    "NewListingsFeed",
    "binance_wallet_announcements",
    "mexc_futures",
    "AsterDexListings",
    "coin_listing",
    "gate_list",
    "gate_fut",
    "bitget_listings",
    "ourbit_listings",
]

# Per-channel phrases that indicate the post should be ignored.
# Keep values in lowercase for case-insensitive matching.
CHANNEL_SKIP_PHRASES = {
    "metascalp_announcements_ru": (
        "there are no listings today",
        "дорогие пользователи",
        "дорогие трейдеры",
        "уважаемые пользователи",
        "новый shorts на нашем",
    ),
}

# Per-channel required-word groups that indicate the post should be ignored.
# Post is skipped if all words from any tuple are present (case-insensitive).
CHANNEL_SKIP_ALL_WORDS = {
    "metascalp_announcements_ru": (
        ("обновление", "metascalp"),
        ("обновления", "metascalp"),
    ),
}

# Delisting feeds are configured separately from listing channels.
# Add channels here one by one as they are approved.
DELISTING_CHANNELS = [
    "DelistingsFeed",
    "delist_binance",
    "cex_delisting",
    "coin_listing",
    "upbitcexradar",
]

# Per-channel phrases for delisting feeds that should be ignored.
# Keep values in lowercase for case-insensitive matching.
DELISTING_CHANNEL_SKIP_PHRASES = {
    "delistingsfeed": (),
    "delist_binance": (),
    "cex_delisting": (),
}

# Listing feeds where only specific messages should be treated as delistings.
# Dual-routing channels are auto-detected by intersection of CHANNELS and
# DELISTING_CHANNELS. Keep this set for additional listing-only sources.
DELISTING_KEYWORD_CHANNELS = set()

# Unified list used by Telethon subscriptions and message handlers.
MONITORED_CHANNELS = list(dict.fromkeys(CHANNELS + DELISTING_CHANNELS))
