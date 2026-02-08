from bot_service import broadcast

async def send_alert(text: str, parse_mode: str | None = None):
    await broadcast(text=text, reply_markup=None, parse_mode=parse_mode)
