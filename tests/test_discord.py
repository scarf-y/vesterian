from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from vesterian.bot import Bot, ResultView
from vesterian.core import Result, UserError


def interaction(uid=11, guild=22, channel=33, iid=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=uid),
        guild_id=guild,
        channel_id=channel,
        id=iid,
        message=SimpleNamespace(id=44),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock(), is_done=lambda: True
        ),
        edit_original_response=AsyncMock(return_value=SimpleNamespace(id=44, edit=AsyncMock())),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.fixture
async def bot(service):
    app = Bot()
    app.service = service
    yield app
    await app.close()


async def test_ten_commands_are_guild_only_with_correct_options(bot):
    commands = {c.name: c for c in bot.tree.get_commands()}
    assert set(commands) == {
        "search",
        "item",
        "ability",
        "monster",
        "map",
        "price",
        "codes",
        "updates",
        "help",
        "status",
    }
    assert all(c.guild_only for c in commands.values())
    assert bot.intents == discord.Intents.none()
    for name in ["item", "ability", "monster", "map", "price", "codes", "updates"]:
        assert next(p for p in commands[name].parameters if p.name == "public").default is False
    assert {c.value for c in commands["item"].parameters[1].choices} == {"overview", "sources", "crafting"}


async def test_private_reply_deferred_before_data_lookup(bot):
    i = interaction()
    original = bot.service.run

    async def checked(*args, **kwargs):
        i.response.defer.assert_awaited_once()
        return await original(*args, **kwargs)

    bot.service.run = checked
    await bot.respond(i, "item", "Stick")
    assert i.response.defer.call_args.kwargs["ephemeral"] is True
    payload = i.edit_original_response.call_args.kwargs
    assert payload["embed"].title.startswith("Stick")
    assert payload["allowed_mentions"].to_dict()["parse"] == []
    assert bot.active == {}


async def test_public_opt_in_and_search_remains_private(bot):
    i = interaction()
    await bot.respond(i, "item", "Stick", public=True)
    assert i.response.defer.call_args.kwargs["ephemeral"] is False
    i = interaction(iid=2)
    await bot.respond(i, "search", "stick", public=True)
    assert i.response.defer.call_args.kwargs["ephemeral"] is True


async def test_throttled_commands_do_not_fetch_or_defer(bot):
    for _ in range(5):
        bot.limits.take(11, 22)
    i = interaction()
    calls = len(bot.service.repo.source.calls)
    await bot.respond(i, "item", "Stick")
    i.response.defer.assert_not_awaited()
    assert "wait" in i.response.send_message.call_args.args[0]
    assert len(bot.service.repo.source.calls) == calls


async def test_owner_guild_channel_and_expiry_checks(bot):
    view = ResultView(bot, interaction(), Result("test", ["a"]), "help")
    view.message = SimpleNamespace(id=44)
    for i in [interaction(uid=12), interaction(guild=23), interaction(channel=34)]:
        assert not await view.interaction_check(i)
        assert i.response.send_message.call_args.kwargs["ephemeral"]
    i = interaction()
    i.message.id = 45
    assert not await view.interaction_check(i)
    view.created -= 601
    assert not await view.interaction_check(interaction())


async def test_pagination_edits_existing_message_without_fetch(bot):
    i = interaction()
    view = ResultView(bot, i, Result("list", [f"row {x}" for x in range(25)]), "help")
    assert view.children[0].disabled
    calls = len(bot.service.repo.source.calls)
    assert await view.interaction_check(i)
    await view.children[1].callback(i)
    assert view.page == 1
    i.response.edit_message.assert_awaited_once()
    assert len(bot.service.repo.source.calls) == calls


async def test_dropdown_changes_view_and_keeps_identity(bot):
    i = interaction()
    result = await bot.service.run("item", "Stick")
    view = ResultView(bot, i, result, "item", "Stick", "overview")
    await view.replace(i, "item", "Stick", "crafting")
    assert "Oak Wood" in view.pages[0].description
    assert view.query == "Stick" and view.section == "crafting"
    assert not view.busy and bot.active == {}


async def test_autocomplete_makes_no_source_requests(bot):
    i = interaction()
    i.command = SimpleNamespace(name="ability")
    before = len(bot.service.repo.source.calls)
    choices = await bot.autocomplete(i, "throw")
    assert choices and len(choices) <= 25
    assert all(c.value.startswith("id:") for c in choices)
    assert len(bot.service.repo.source.calls) == before


async def test_error_clears_controls_without_exposing_exception(bot):
    bot.service.run = AsyncMock(side_effect=ValueError("secret-token-example"))
    i = interaction()
    await bot.respond(i, "item", "Stick")
    args = i.edit_original_response.call_args.kwargs
    assert "secret" not in args["content"]
    assert args["view"] is None
    assert bot.active == {}


async def test_duplicate_interaction_and_dm_rejected(bot):
    i = interaction()
    bot.admit(i, "help")
    with pytest.raises(UserError, match="already handled"):
        bot.admit(i, "help")
    with pytest.raises(UserError, match="servers"):
        bot.admit(interaction(guild=None, iid=2), "help")
