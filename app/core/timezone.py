from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except ZoneInfoNotFoundError:  # Windows without tzdata installed
    # Fallback: fixed UTC+5 offset (Asia/Tashkent currently UTC+05:00 year‑round, no DST)
    TASHKENT_TZ = timezone(timedelta(hours=5))

UTC = timezone.utc

def to_tashkent(dt: datetime | None) -> datetime | None:
    """Convert a datetime to Asia/Tashkent timezone (fallback to fixed +05:00 if tz db missing).

    Rules:
    - None stays None
    - Naive datetime is treated as UTC
    - Aware datetime is converted via astimezone
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TASHKENT_TZ)

__all__ = ["to_tashkent", "TASHKENT_TZ"]

COMPACT_FMT = "%Y-%m-%d %H:%M"

def format_tashkent_compact(dt: datetime | None) -> str | None:
    """Return datetime formatted as 'YYYY-MM-DD HH:MM' in Asia/Tashkent.
    Drops seconds & timezone offset for cleaner UI (intentional loss of precision)."""
    local = to_tashkent(dt)
    if not local:
        return None
    return local.strftime(COMPACT_FMT)
