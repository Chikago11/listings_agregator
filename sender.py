from bot_service import broadcast

async def send_alert(
    text: str,
    parse_mode: str | None = None,
    alert_type: str = "listing",
    exchange: str | None = None,
):
    await broadcast(
        text=text,
        reply_markup=None,
        parse_mode=parse_mode,
        alert_type=alert_type,
        exchange=exchange,
    )
