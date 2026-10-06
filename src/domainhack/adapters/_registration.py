"""Helpers for registration details (statuses, expiration) shared by adapters.

Everything here is defensive: unexpected input yields ``None`` (or is
skipped), never an exception, so a malformed detail can never turn a TAKEN
answer into an ERROR.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

# ISO 8601 / RFC 3339 date-times as registries write them:
# "2026-11-30T07:38:29.000Z", "2027-09-30T01:00:00Z", "2027-01-05 10:00:00+01:00",
# "2027-01-05". A missing offset is read as UTC. (datetime.fromisoformat
# cannot parse "Z" or arbitrary fractions on Python 3.10.)
_ISO_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d+)?)?)?"
    r"\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?"
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NON_WORD = re.compile(r"[\s_-]+")
_MAX_STATUS_LENGTH = 64


def parse_datetime_utc(value: object) -> datetime | None:
    """Parse an ISO 8601 date or date-time into an aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    match = _ISO_DATETIME.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second, offset = match.groups()
    try:
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=_parse_offset(offset),
        )
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _parse_offset(offset: str | None) -> timezone:
    if offset is None or offset in ("Z", "z"):
        return timezone.utc
    sign = -1 if offset[0] == "-" else 1
    digits = offset[1:].replace(":", "")
    minutes = int(digits[:2]) * 60 + int(digits[2:4] or 0)
    return timezone(sign * timedelta(minutes=minutes))  # ValueError if out of range


def format_utc(value: datetime | None) -> str:
    """ISO 8601 in UTC with a ``Z`` suffix (``2026-11-30T07:38:29Z``), or ``""``."""
    if value is None:
        return ""
    utc = value.astimezone(timezone.utc).replace(tzinfo=None)
    return utc.isoformat(timespec="seconds") + "Z"


def normalize_status(value: object) -> str | None:
    """Normalize a status to RFC 9083 form: ``"pendingDelete"`` -> ``"pending delete"``.

    RDAP values are already in that form; EPP codes (seen in WHOIS replies)
    are camelCase. Returns None for anything that is not a short word string.
    """
    if not isinstance(value, str):
        return None
    spaced = _NON_WORD.sub(" ", _CAMEL_BOUNDARY.sub(" ", value)).strip().lower()
    if not spaced or len(spaced) > _MAX_STATUS_LENGTH or not spaced.isprintable():
        return None
    return spaced


def normalize_statuses(values: Iterable[object]) -> tuple[str, ...]:
    """Normalize every status, dropping invalid ones and duplicates (order kept)."""
    seen: dict[str, None] = {}
    for value in values:
        status = normalize_status(value)
        if status is not None:
            seen.setdefault(status, None)
    return tuple(seen)
