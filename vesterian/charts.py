"""PNG charts with Discord-controlled ranges and metrics."""

import asyncio
import io
import math
from datetime import datetime, timedelta, UTC
from pathlib import Path
from threading import Lock

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import dates as mdates, image as mimage
from matplotlib.ticker import FuncFormatter

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


def render(name, data, window, metric, now):
    points, start, end = series(data, window, metric, now)
    valid = [v for _, v in points if math.isfinite(v)]
    unit, divisor, _ = next((d for d in DENOMINATIONS if valid and max(valid) >= d[1]), DENOMINATIONS[-1])
    with LOCK:
        fig = Figure(figsize=(10, 6), dpi=120, facecolor="#101923")
        FigureCanvasAgg(fig)
        ax = fig.add_axes((0.1, 0.43, 0.85, 0.38), facecolor="#101923")
        for spine in ax.spines.values():
            spine.set_color("#334454")
        ax.tick_params(colors="#aab9c8", labelsize=9)
        ax.grid(axis="y", color="#273747", linewidth=0.7)
        ax.set_xlim(start, end)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M" if window == "24h" else "%d %b", tz=UTC))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f"{n:,.2f}".rstrip("0").rstrip(".")))
        ax.set_ylabel(unit + " / item", color="#aab9c8")
        if valid:
            ax.plot(
                [p[0] for p in points],
                [p[1] / divisor for p in points],
                color="#70f1ff",
                linewidth=2,
                marker="o",
                markersize=3,
            )
            ax.set_ylim(bottom=0)
            last = next(p for p in reversed(points) if math.isfinite(p[1]))
            ax.annotate(
                f"{last[1] / divisor:,.2f} {unit}",
                xy=(last[0], last[1] / divisor),
                xytext=(-8, 12),
                textcoords="offset points",
                ha="right",
                color="#70f1ff",
                fontsize=10,
            )
            ax.set_ylim(0, max(valid) / divisor * 1.25 if max(valid) else 1)
        else:
            ax.text(
                0.5,
                0.5,
                "No comparable prices — try another range",
                ha="center",
                color="#aab9c8",
                transform=ax.transAxes,
            )
        volume = fig.add_axes((0.1, 0.17, 0.85, 0.15), facecolor="#101923", sharex=ax)
        rows = volume_rows(data, window, now)
        width = 0.025 if window == "24h" else 0.65
        volume.bar(
            [r[0] for r in rows],
            [count(r[1].get("units")) for r in rows],
            width=width,
            color="#477d85",
            label="Items sold",
        )
        volume.bar(
            [r[0] for r in rows],
            [count(r[1].get("purchases")) for r in rows],
            width=width * 0.4,
            color="#d7e4ef",
            label="Transactions",
        )
        volume.tick_params(colors="#aab9c8", labelsize=9)
        volume.set_ylabel("Count", color="#aab9c8")
        volume.legend(
            loc="upper left", bbox_to_anchor=(0, 1.4), ncol=2, frameon=False, labelcolor="#aab9c8", fontsize=9
        )
        for spine in volume.spines.values():
            spine.set_color("#334454")
        ax.tick_params(labelbottom=False)
        fig.text(0.1, 0.91, name[:65], color="white", fontsize=17, weight="bold", parse_math=False)
        fig.text(0.1, 0.85, f"{METRICS[metric]}  /  {window}  /  UTC", color="#aab9c8", fontsize=10)
        fig.text(
            0.1,
            0.08,
            "Per-item prices only · Legacy per-sale days excluded · Missing values are gaps",
            color="#aab9c8",
            fontsize=9,
        )
        icon = Path(__file__).parent / "assets" / f"{unit.lower()}.png"
        icon_ax = fig.add_axes((0.9, 0.84, 0.055, 0.11))
        icon_ax.imshow(mimage.imread(icon))
        icon_ax.axis("off")
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
