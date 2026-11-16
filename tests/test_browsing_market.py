from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import discord
import pytest

from test_discord import interaction
from vesterian.bot import Bot, ResultView
from vesterian.core import UserError
from vesterian.data import SourceError
from vesterian.market import activity, change, freshness
from vesterian.supervisor import supervise


async def test_summary_explains_change_and_volume(service):
    result = await service.run("price", "Holy Scroll")
    assert any("+6.4%" in value for _, value, _ in result.fields)
    assert "1 transaction and 10 items" in " ".join(result.lines)
    assert "Source snapshot" in " ".join(result.lines)


def test_sparse_unknown_and_zero_market_context():
    assert "Low volume" in activity({"purchases": 1, "units": 10})
    assert "No completed sales" in activity({"purchases": 0})
    assert "Unknown transactions" in activity({})
    assert "Not comparable" in change({"averagePrice": 10, "previousAveragePrice": 0})
    assert "Not comparable" in change({"averagePrice": 10, "previousAveragePrice": 5})
    assert "-100.0%" in change(
        {"averagePrice": 0, "previousAveragePrice": 5, "purchases": 1, "previousPurchases": 1}
    )
    assert "unavailable" in freshness("bad", 0)
    assert "ahead" in freshness("2099-01-01T00:00:00Z", 0)
    assert "Older than 24 hours" in freshness("2020-01-01T00:00:00Z", 1794704400)


async def test_related_links_are_catalog_only_and_variant_exact(service):
    entry = service.repo.index.search("Stick", "item")[0]
    data = {
        "links": [
            {"url": "/items/holy-scroll?variation=magnificent"},
            {"url": "https://evil.test/items/stick"},
            {"url": "/items/not-in-catalog"},
            {"url": "/items/stick"},
        ]
    }
    related = service.related(data, entry)
    assert len(related) == 1
    assert related[0].path.endswith("variation=magnificent")


async def test_shortcuts_related_menu_and_back(service):
    service.repo.source.responses["/items/stick"]["metadata"]["obtainedFrom"] = {
        "name": "Acid Slime",
        "url": "/monsters/acid-slime",
    }
    app = Bot()
    app.service = service
    try:
        i = interaction()
        result = await service.run("item", "Stick")
        view = ResultView(app, i, result, "item", "Stick", "overview")
        assert {"Check price", "Find sources"} <= {getattr(c, "label", None) for c in view.children}
        menu = next(
            c
            for c in view.children
            if isinstance(c, discord.ui.Select) and c.placeholder.startswith("Explore")
        )
        menu._values = [menu.options[0].value]
        assert await view.interaction_check(i)
        await menu.callback(i)
        assert view.command == "monster"
        back = next(c for c in view.children if getattr(c, "label", None) == "Back")
        await back.callback(interaction(iid=2))
        assert view.command == "item" and not view.history
        assert not app.active
        await view.on_timeout()
        assert all(c.disabled for c in view.children if not getattr(c, "url", None))
    finally:
        await app.close()


async def test_chart_components_fit_discord_rows(service):
    app = Bot()
    app.service = service
    try:
        result = await service.run("price", "Holy Scroll", "chart")
        view = ResultView(app, interaction(), result, "price", "Holy Scroll", "chart")
        view.history = [("item", "Stick", "overview", {})]
        view.rebuild()
        assert len(view.to_components()) <= 5
        for iid in (1, 2):
            app.admit(interaction(iid=iid), "price", component=True)
        with pytest.raises(UserError, match="wait"):
            app.admit(interaction(iid=3), "price", component=True)
        assert app.metrics["rate_limited"] == 1
    finally:
        await app.close()


@pytest.mark.parametrize(
    "malformed",
    [
        {"markets": ["broken"]},
        {"last24Hours": []},
        {"markets": [{"averagePrice": float("nan")}]},
        {"markets": [{"purchases": -1}]},
    ],
)
async def test_schema_drift_keeps_valid_cache(service, malformed):
    repo = service.repo
    path = "/market/holy-scroll"
    good = await repo.get(path)
    repo.source.responses[path].update(malformed)
    fallback = await repo.get(path, fresh=0)
    assert fallback.stale and fallback.data == good.data
    assert repo.metrics["invalid_payloads"] == repo.metrics["stale_served"] == 1
    repo.cache.clear()
    with pytest.raises(SourceError):
        await repo.get(path)


def test_supervisor_backoff_limit_and_clean_exit():
    run = Mock(return_value=SimpleNamespace(returncode=1))
    sleep = Mock()
    assert supervise([], run, sleep) == 1
    assert run.call_count == 6
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40, 60]
    run.reset_mock()
    assert supervise(["--sync"], run, sleep) == 2
    run.assert_not_called()
    run.return_value.returncode = 0
    assert supervise([], run, sleep) == 0
    assert run.call_count == 1


def test_supervisor_ctrl_c_stops():
    assert supervise([], Mock(side_effect=KeyboardInterrupt), Mock()) == 0


async def test_failed_navigation_retains_current_card(service):
    app = Bot()
    app.service = service
    try:
        i = interaction()
        result = await service.run("item", "Stick")
        view = ResultView(app, i, result, "item", "Stick", "overview")
        await view.replace(i, "item", "not-a-real-item", None, navigate=True)
        assert view.result is result and view.command == "item"
        assert not view.history and not view.busy and not app.active
        i.followup.send.assert_awaited_once()
        i.edit_original_response.assert_not_awaited()
    finally:
        await app.close()


async def test_unexpected_refresh_failure_does_not_kill_worker(service, monkeypatch, caplog):
    app = Bot()
    app.service = service
    refresh = AsyncMock(side_effect=[RuntimeError("secret-example"), None])
    monkeypatch.setattr(service.repo, "refresh", refresh)
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    monkeypatch.setattr("vesterian.bot.asyncio.sleep", sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await app.refresh_loop()
        assert refresh.await_count == 2
        assert service.repo.metrics["refresh_failures"] == 1
        assert "secret-example" not in caplog.text
    finally:
        await app.close()
