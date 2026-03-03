import csv
import os
from datetime import datetime
from threading import Lock


LOG_FIELDS = [
    "logged_at",
    "source",
    "original_post",
    "status",
    "post_for_user",
]

_LOCK = Lock()


def append_post_log(
    *,
    log_path: str,
    source: str,
    original_post: str,
    status: str,
    post_for_user: str = "",
) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    with _LOCK:
        file_exists = os.path.exists(log_path)

        with open(log_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if not file_exists:
                writer.writeheader()

            writer.writerow(
                {
                    "logged_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
                    "source": source or "",
                    "original_post": original_post or "",
                    "status": status or "",
                    "post_for_user": post_for_user or "",
                }
            )
