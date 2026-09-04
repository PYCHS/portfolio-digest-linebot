from __future__ import annotations

from datetime import date as Date


def parse_coupon_month_days(value: str) -> list[tuple[int, int]]:
    """Parse semicolon-separated M/D values, rejecting partial corruption."""
    out: list[tuple[int, int]] = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            month_s, day_s = part.split("/")
            month, day = int(month_s), int(day_s)
            Date(2000, month, day)  # leap year permits a legitimate 2/29
        except ValueError as exc:
            raise ValueError(f"bad coupon date {part!r}") from exc
        if (month, day) in out:
            raise ValueError(f"duplicate coupon date {part!r}")
        out.append((month, day))
    return out
