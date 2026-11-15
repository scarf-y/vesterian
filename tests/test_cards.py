import pytest
from vesterian.core import Result
from vesterian.charts import volume_rows


@pytest.mark.parametrize(
    ("command", "name", "field"),
    [
        ("item", "Stick", "Attack"),
        ("monster", "Acid Slime", "Health"),
        ("ability", "Rock Throw", "Cooldown"),
        ("map", "Nilgarf", "Shops"),
    ],
)
async def test_compact_inline_overviews(service, command, name, field):
    result = await service.run(command, name)
    assert len(result.embeds()) == 1
    assert any(f.name == field and f.inline for f in result.embeds()[0].fields)
    assert len(result.embeds()[0].description) < 600


async def test_details_keep_secondary_information(service):
    result = await service.run("item", "Stick", "details")
    assert "Blessed upgrade" in "\n".join(result.lines)
    assert not result.fields


async def test_market_summary_and_chart_fields(service):
    summary = await service.run("price", "Holy Scroll")
    assert any("Activity" in n and "transactions" in v for n, v, _ in summary.fields)
    chart = await service.run("price", "Holy Scroll", "chart")
    assert {"Latest recorded", "Change", "Items sold", "Transactions"} <= {n for n, _, _ in chart.fields}
    assert "Price comparison:" in "\n".join(chart.lines)


def test_field_pagination_respects_limits_without_losing_text():
    result = Result("Test", ["x" * 3000], fields=[(f"Stat {i}", "y" * 2000, True) for i in range(30)])
    embeds = result.embeds()
    assert all(len(e) <= 6000 and len(e.fields) <= 25 for e in embeds)
    assert sum(len(f.value) for e in embeds for f in e.fields) == 60000


def test_volume_excludes_legacy_future_and_malformed():
    data = {
        "days": [
            {"day": "2026-11-14", "units": 100, "purchases": 5},
            {"day": "2026-11-13", "units": 999, "legacy": True},
            {"day": "2099-01-01", "units": 200},
            {"day": "invalid", "units": 3},
        ]
    }
    rows = volume_rows(data, "7d", 1794704400)
    assert sum(r["units"] for _, r in rows) == 100
