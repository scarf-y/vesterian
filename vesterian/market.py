"""Plain-language market context without inferring missing observations."""

import math
from datetime import datetime


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def activity(row):
    purchases, units = row.get("purchases"), row.get("units")
    trades = f"{purchases:,}" if number(purchases) else "Unknown"
    items = f"{units:,}" if number(units) else "unknown"
    text = f"{trades} transactions · {items} items sold"
    if number(purchases) and purchases < 10:
        text += (
            "\nNo completed sales recorded."
            if purchases == 0
            else "\nLow volume (fewer than 10 transactions): a few sales can move the average sharply."
        )
    return text


def change(row):
    current, previous = row.get("averagePrice"), row.get("previousAveragePrice")
    if (
        number(current)
        and number(previous)
        and previous > 0
        and number(row.get("purchases"))
        and row["purchases"] > 0
        and number(row.get("previousPurchases"))
        and row["previousPurchases"] > 0
    ):
        return f"{(current / previous - 1) * 100:+.1f}% versus previous period"
    return "Not comparable — missing prior sales or a positive prior average."


def freshness(value, now):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        age = now - stamp.timestamp()
        if age < -300:
            return "Source time is ahead of the bot clock; age cannot be verified."
        minutes = max(0, int(age / 60))
        text = f"Source snapshot: {minutes // 60}h {minutes % 60}m old."
        if age > 86400:
            text += " Older than 24 hours; prices may have changed."
        return text
    except (AttributeError, TypeError, ValueError, OverflowError):
        return "Source snapshot age unavailable."
