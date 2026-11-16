"""PNG charts with Discord-controlled ranges and metrics."""

import asyncio
import io
import math
from datetime import datetime, timedelta, UTC
from pathlib import Path
from threading import Lock
from statistics import median

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import dates as mdates, image as mimage
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .presentation import DENOMINATIONS

LOCK = Lock()
RANGES = {"24h": 1, "7d": 7, "30d": 30}
METRICS = {
    "averagePrice": "Average sale",
    "medianPrice": "Median sale",
    "averageAskingPrice": "Average asking price",
}


def volume_rows(data, window, now):
    _, start, end = series(data, window, "averagePrice", now)
    rows = []
    for row in data.get("hours" if window == "24h" else "days", [])[:2000]:
        if row.get("legacy"):
            continue
        try:
            at = datetime.fromisoformat(str(row.get("time") or row.get("day")).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if start <= at <= end:
                rows.append((at, row))
        except (ValueError, TypeError):
            continue
    return sorted(rows, key=lambda r: r[0])


def count(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else 0
    )


def series(data, window, metric, now):
    if window not in RANGES or metric not in METRICS:
        raise ValueError("Unsupported chart selection")
    end = datetime.fromtimestamp(now, UTC)
    start = end - timedelta(days=RANGES[window])
    points = []
    for row in data.get("hours" if window == "24h" else "days", [])[:2000]:
        if row.get("legacy"):
            continue
        try:
            at = datetime.fromisoformat(str(row.get("time") or row.get("day")).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            value = row.get(metric)
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            )
            if metric != "averageAskingPrice" and row.get("purchases") == 0:
                valid = False
            if start <= at <= end:
                points.append((at, value if valid else float("nan")))
        except (ValueError, TypeError):
            continue
    # Insert a break when an entire time bucket is absent, too.
    ordered = sorted(points)
    step = timedelta(hours=1) if window == "24h" else timedelta(days=1)
    expanded = []
    for point in ordered:
        if expanded and point[0] - expanded[-1][0] > step * 1.5:
            expanded.append((expanded[-1][0] + step, float("nan")))
        expanded.append(point)
    return expanded, start, end


def tick_price(value, _=None):
    # Keep tiny nonzero values visible instead of rounding them to zero.
    if value and abs(value) < 0.01:
        return f"{value:.2g}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def price_scale(values):
    """Fit all observations with breathing room, including flat and zero-price series."""
    low, high = min(values), max(values)
    padding = (high - low) * 0.15 if high > low else (abs(high) * 0.02 or 1)
    bottom, top = max(0, low - padding), high + padding
    # Tick precision follows the plotted span, not a fixed number of decimals.
    decimals = max(0, math.ceil(-math.log10((top - bottom) / 5)) + 1)

    def label(value, _):
        if decimals > 10:
            return f"{value:.12g}"
        text = f"{value:,.{decimals}f}"
        return text.rstrip("0").rstrip(".") if decimals else text

    return bottom, top, label


def build_figure(name, data, window, metric, now):
    points, start, end = series(data, window, metric, now)
    observed = [(at, v) for at, v in points if math.isfinite(v)]
    valid = [v for _, v in observed]
    unit, divisor, _ = next((d for d in DENOMINATIONS if valid and median(valid) >= d[1]), DENOMINATIONS[-1])
    rows = volume_rows(data, window, now)
    bg, panel, text, muted, accent = "#101720", "#192430", "#edf4fa", "#a9bbcb", "#75dfcf"
    fig = Figure(figsize=(10, 6), dpi=120, facecolor=bg)
    FigureCanvasAgg(fig)
    title = name if len(name) <= 55 else name[:52] + "..."
    fig.text(0.085, 0.94, title, color=text, fontsize=19, weight="bold", parse_math=False)
    bucket = "Hourly" if window == "24h" else "Daily"
    subtitle = f"{METRICS[metric]} per item  /  Last {window}  /  {bucket} observations  /  UTC"
    fig.text(0.085, 0.895, subtitle, color=muted, fontsize=10)
    latest = f"{tick_price(valid[-1] / divisor)} {unit}" if valid else "No price data"
    change = (valid[-1] / valid[0] - 1) * 100 if len(valid) > 1 and valid[0] > 0 else None
    change_text = f"{change:+.1f}%" if change is not None else "Not comparable"
    trades_known = bool(rows) and all(
        isinstance(r.get("purchases"), (int, float))
        and not isinstance(r.get("purchases"), bool)
        and math.isfinite(r["purchases"])
        and r["purchases"] >= 0
        for _, r in rows
    )
    trades = sum(count(r.get("purchases")) for _, r in rows)
    activity = f"{trades:,.0f} transactions" if trades_known else "Volume incomplete"
    activity_hint = (
        "Low volume: fewer than 10" if trades_known and trades < 10 else "Across available observations"
    )
    latest_hint = (
        observed[-1][0].strftime("Recorded %d %b, %H:%M UTC") if observed else "Try a longer time range"
    )
    cards = [
        ("LATEST RECORDED", latest, latest_hint),
        ("OBSERVED CHANGE", change_text, "First to last available price"),
        ("TRADING ACTIVITY", activity, activity_hint),
    ]
    for x, (label, value, hint) in zip((0.085, 0.385, 0.685), cards, strict=True):
        card = fig.add_axes((x, 0.745, 0.28, 0.115), facecolor=panel)
        card.set_xticks([])
        card.set_yticks([])
        for spine in card.spines.values():
            spine.set_visible(False)
        card.text(0.06, 0.77, label, color=muted, fontsize=8, transform=card.transAxes)
        card.text(
            0.06,
            0.40,
            value,
            color=accent if label == "LATEST RECORDED" else text,
            fontsize=14,
            weight="bold",
            transform=card.transAxes,
        )
        card.text(0.06, 0.12, hint, color=muted, fontsize=7.5, transform=card.transAxes)
    ax = fig.add_axes((0.085, 0.395, 0.88, 0.285), facecolor=bg)
    volume = fig.add_axes((0.085, 0.16, 0.88, 0.145), facecolor=bg, sharex=ax)
    for plot in (ax, volume):
        for spine in plot.spines.values():
            spine.set_visible(False)
        plot.tick_params(colors=muted, labelsize=9, length=0, pad=7)
        plot.grid(axis="y", color="#293644", linewidth=0.6)
        plot.set_axisbelow(True)
        plot.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=2))
    ax.set_xlim(start, end)
    locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
    volume.xaxis.set_major_locator(locator)
    volume.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M" if window == "24h" else "%d %b", tz=UTC))
    ax.tick_params(labelbottom=False)
    ax.yaxis.set_major_formatter(FuncFormatter(tick_price))
    ax.set_title(f"PRICE  /  {unit} per item (auto-scaled)", loc="left", color=muted, fontsize=9, pad=12)
    if valid:
        ax.text(
            1,
            1.08,
            "Dashed guide: latest recorded price",
            ha="right",
            color=muted,
            fontsize=8,
            transform=ax.transAxes,
        )
        times, values = [at for at, _ in points], [v / divisor for _, v in points]
        ax.plot(times, values, color=accent, linewidth=2.3, marker="o", markersize=3.5)
        ax.axhline(valid[-1] / divisor, color=accent, alpha=0.3, linewidth=1, linestyle=(0, (3, 4)))
        ax.scatter(
            [observed[-1][0]],
            [valid[-1] / divisor],
            s=45,
            color=accent,
            edgecolor=bg,
            linewidth=1.5,
            zorder=5,
            clip_on=False,
        )
        bottom, top, formatter = price_scale([v / divisor for v in valid])
        ax.set_ylim(bottom, top)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=3, prune="both"))
        ax.yaxis.set_major_formatter(FuncFormatter(formatter))
    else:
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.text(
            0.5,
            0.5,
            "No comparable prices in this range",
            ha="center",
            va="center",
            color=text,
            fontsize=12,
            transform=ax.transAxes,
        )
    # Side-by-side bars avoid making transactions look like part of an item stack.
    width = 0.014 if window == "24h" else 0.28
    dates = [mdates.date2num(at) for at, _ in rows]
    for key, offset, color, label in (
        ("units", -width / 2, "#527eae", "Items sold"),
        ("purchases", width / 2, "#d9bc7d", "Transactions"),
    ):
        values = [r.get(key) for _, r in rows]
        heights = [
            v
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
            else float("nan")
            for v in values
        ]
        volume.bar([at + offset for at in dates], heights, width=width * 0.9, color=color, label=label)
    volume.set_ylim(bottom=0)
    if not rows:
        volume.text(
            0.5,
            0.5,
            "No trading activity data in this range",
            ha="center",
            va="center",
            color=muted,
            fontsize=10,
            transform=volume.transAxes,
        )
    volume.yaxis.set_major_locator(MaxNLocator(nbins=3, integer=True, min_n_ticks=2))
    volume.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f"{n:,.0f}"))
    if not rows:
        volume.set_yticks([])
    volume.set_title("TRADING ACTIVITY  /  count", loc="left", color=muted, fontsize=9, pad=12)
    volume.legend(
        loc="lower right",
        bbox_to_anchor=(1, 1.04),
        ncol=2,
        frameon=False,
        labelcolor=text,
        fontsize=8,
        borderaxespad=0,
    )
    fig.text(
        0.085,
        0.075,
        "1 transaction = 1 purchase; items sold counts the quantity bought.",
        color=muted,
        fontsize=8,
    )
    explanation = (
        "Asks are seller listings, not completed sales."
        if metric == "averageAskingPrice"
        else "Each point is a recorded per-item price."
    )
    fig.text(
        0.085, 0.04, explanation + " Gaps = missing data; legacy pricing excluded.", color=muted, fontsize=8
    )
    icon = Path(__file__).parent / "assets" / f"{unit.lower()}.png"
    icon_ax = fig.add_axes((0.925, 0.89, 0.04, 0.07))
    icon_ax.imshow(mimage.imread(icon))
    icon_ax.axis("off")
    return fig


def render(name, data, window, metric, now):
    with LOCK:
        fig = build_figure(name, data, window, metric, now)
        out = io.BytesIO()
        fig.savefig(out, format="png", facecolor=fig.get_facecolor())
        return out.getvalue()


class ChartRenderer:
    def __init__(self):
        self.slots = asyncio.Semaphore(1)

    async def draw(self, *args):
        await asyncio.wait_for(self.slots.acquire(), timeout=3)
        task = asyncio.create_task(asyncio.to_thread(render, *args))
        task.add_done_callback(lambda _: self.slots.release())
        return await asyncio.shield(task)
