from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return naive UTC to match the existing timezone-naive database columns."""
    return datetime.now(UTC).replace(tzinfo=None)
