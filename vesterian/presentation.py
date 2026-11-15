"""Game currency and safe media presentation."""

import math
import re
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

ICONS = {}
DENOMINATIONS = [
    ("Platinum", 1000000000, "🔹"),
    ("Gold", 1000000, "🟡"),
    ("Silver", 1000, "⚪"),
    ("Bronze", 1, "🟤"),
]


def money(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return "Not recorded"
    remaining = Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    parts = []
    for name, divisor, fallback in DENOMINATIONS:
        amount = remaining // divisor if divisor > 1 else remaining
        remaining -= amount * divisor
        if amount or (divisor == 1 and not parts):
            number = f"{amount:,.2f}".rstrip("0").rstrip(".") if divisor == 1 else f"{amount:,.0f}"
            parts.append(f"{ICONS.get(name, fallback)} {number} {name}")
    return " ".join(parts)


def media_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in "\\\r\n%#"):
        return None
    p = urlsplit(value)
    if (
        value.startswith("//")
        or (p.scheme and p.scheme != "https")
        or (p.netloc and p.netloc != "db.playvesteria.com")
    ):
        return None
    if not re.fullmatch(
        r"/(?:items/[a-z0-9-]+/thumbnail|assets/[0-9]+/(?:image|thumbnail)|monster-image/[a-z0-9-]+\.png|map-image/[a-z0-9-]+\.webp|maps/[a-z0-9-]+/key-art\.png)",
        p.path,
    ):
        return None
    return "https://db.playvesteria.com" + p.path + ("?" + p.query if p.query else "")


def color(rgb, default=0x66B99A):
    if isinstance(rgb, dict) and all(
        isinstance(rgb.get(k), int) and 0 <= rgb[k] <= 255 for k in ("r", "g", "b")
    ):
        return (rgb["r"] << 16) | (rgb["g"] << 8) | rgb["b"]
    return default
