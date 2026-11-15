import io
import math
from datetime import datetime, UTC

import discord
import pytest
from PIL import Image

from vesterian.charts import series, render
from vesterian.presentation import money, media_url, ICONS
from vesterian.bot import ResultView
from test_discord import interaction


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 Bronze"),
        (0.25, "0.25 Bronze"),
        (1000, "1 Silver"),
        (1000000, "1 Gold"),
        (1000000000, "1 Platinum"),
        (999999.999, "1 Gold"),
        (float("nan"), "Not recorded"),
        (-1, "Not recorded"),
    ],
)
def test_currency_boundaries(value, expected):
    assert expected in money(value)


def test_currency_preserves_remainder_and_emoji(monkeypatch):
    monkeypatch.setitem(ICONS, "Gold", "<:vesteria_gold:123>")
    result = money(3667221.56)
    assert "<:vesteria_gold:123> 3 Gold" in result
    assert "667 Silver" in result and "221.56 Bronze" in result


def test_media_rejects_foreign_and_traversal():
    for url in [
        "https://evil.test/items/stick/thumbnail",
        "//evil.test/image",
        "/assets/../token",
        "/items/%2e/thumbnail",
    ]:
        assert media_url(url) is None
    assert media_url("/items/holy-scroll/thumbnail?variation=magnificent").endswith("variation=magnificent")


async def test_embeds_use_exact_variant_image_and_tier(service):
    result = await service.run("item", "Magnificent Holy Scroll")
    embed = result.embeds()[0]
    assert "Tier 9" in embed.description
    assert embed.color.value == 0x00FF00
    assert "variation=magnificent" in embed.thumbnail.url
    unknown = await service.run("item", "Stick")
    assert not unknown.badge


async def test_monster_and_map_images(service):
    monster = await service.run("monster", "Acid Slime")
    assert "/monster-image/acid-slime.png" in monster.thumbnail
    service.repo.source.responses["/monsters/acid-slime"]["hasImage"] = False
    service.repo.cache.clear()
    assert (await service.run("monster", "Acid Slime")).thumbnail is None
    place = await service.run("map", "Nilgarf")
    assert "/map-image/nilgarf" in place.embeds()[0].image.url


def test_chart_excludes_legacy_and_preserves_zero_and_missing():
    now = datetime(2026, 11, 15, tzinfo=UTC).timestamp()
    data = {
        "days": [
            {"day": "2026-11-10", "averagePrice": 9000000, "legacy": True},
            {"day": "2026-11-11", "averagePrice": 0, "purchases": 1},
            {"day": "2026-11-12", "averagePrice": None},
            {"day": "2026-11-13", "averagePrice": 100, "purchases": 0},
            {"day": "2026-11-16", "averagePrice": 999},
        ]
    }
    points, _, _ = series(data, "7d", "averagePrice", now)
    assert len(points) == 3
    assert points[0][1] == 0
    assert math.isnan(points[1][1]) and math.isnan(points[2][1])


async def test_chart_is_png_and_controls_reuse_cached_history(service):
    first = await service.run("price", "Holy Scroll", "chart", window="7d")
    assert first.chart.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(first.chart)).size == (1200, 720)
    second = await service.run("price", "Holy Scroll", "chart", window="24h", metric="medianPrice")
    assert second.chart != first.chart
    assert second.chart_range == "24h"
    assert sum("market-history" in path for path, _ in service.repo.source.calls) == 1


async def test_chart_attachments_replaced_and_removed(service):
    from vesterian.bot import Bot

    app = Bot()
    app.service = service
    i = interaction()
    result = await service.run("price", "Holy Scroll", "chart")
    view = ResultView(app, i, result, "price", "Holy Scroll", "chart")
    assert len([child for child in view.children if isinstance(child, discord.ui.Select)]) == 3
    assert view.attachments()[0].filename == "price.png"
    await view.replace(i, "price", "Holy Scroll", "chart", window="30d", metric="medianPrice")
    payload = i.edit_original_response.call_args.kwargs
    assert len(payload["attachments"]) == 1
    assert payload["embed"].image.url == "attachment://price.png"
    await view.replace(i, "price", "Holy Scroll", "summary")
    assert i.edit_original_response.call_args.kwargs["attachments"] == []
    await app.close()


def test_empty_chart_is_valid_image():
    image = render("No sales", {"days": []}, "7d", "averagePrice", 1794704400)
    assert image.startswith(b"\x89PNG")
