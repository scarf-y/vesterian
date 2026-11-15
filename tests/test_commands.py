import pytest

from vesterian.core import UserError
from vesterian.data import SourceError


def text(result):
    return "\n".join(result.lines)


@pytest.mark.parametrize(
    ("command", "query", "view", "expected"),
    [
        ("item", "Stick", "overview", "15"),
        ("item", "Stick", "sources", "variation=shroom"),
        ("item", "Stick", "crafting", "Oak Wood"),
        ("ability", "Rock Throw", "overview", "STAM"),
        ("ability", "Rock Throw", "ranks", "Rank 3"),
        ("monster", "Acid Slime", "overview", "40,020 HP"),
        ("monster", "Acid Slime", "drops", "0.0976%"),
        ("map", "Nilgarf", "npcs", "Streisand"),
        ("map", "Nilgarf", "exits", "destination has no map page"),
        ("map", "Nilgarf", "monsters", "Chicken"),
        ("price", "Holy Scroll", "summary", "221.56 Bronze"),
        ("price", "Holy Scroll", "history", "legacy per-sale pricing"),
        ("updates", "", "", "Lavalands"),
        ("updates", "1359", "", "Lavalands"),
        ("codes", "", "", "StarterPack"),
        ("help", "", "", "/monster"),
        ("status", "", "", "Catalog: ready"),
    ],
)
async def test_real_response_command_views(service, command, query, view, expected):
    result = await service.run(command, query, view or None)
    assert expected in text(result)
    for embed in result.embeds():
        assert len(embed) <= 6000
        assert len(embed.description) <= 4096


async def test_exact_variant_does_not_fetch_parent(service):
    result = await service.run("item", "Magnificent Holy Scroll")
    assert "Magnificent" in result.title
    assert "variation=magnificent" in result.url
    assert service.repo.source.calls[-1][0] == "/items/holy-scroll?variation=magnificent"


async def test_ability_variant_ranks(service):
    result = await service.run("ability", "Boulder Throw", "ranks")
    assert "Boulder Throw" in result.title
    assert "variation=boulder-throw" in result.url


async def test_ambiguous_names_offer_choices_without_fetch(service):
    before = len(service.repo.source.calls)
    result = await service.run("item", "scroll")
    assert len(result.choices) > 1
    assert len(service.repo.source.calls) == before


async def test_search_is_local_and_selected_id_resolves(service):
    result = await service.run("search", "stick")
    assert result.choices[0].kind == "item"
    chosen = await service.run("item", result.choices[0].key)
    assert chosen.title.startswith("Stick")


async def test_unknown_and_invalid_input(service):
    for command, query, view in [
        ("item", "not-real", None),
        ("item", "x" * 101, None),
        ("item", "Stick", "execute"),
        ("updates", "abc", None),
    ]:
        with pytest.raises(UserError):
            await service.run(command, query, view)


async def test_known_expired_codes_excluded_and_requirements_preserved(service):
    code = service.repo.source.responses["/codes"]["codes"][0]
    code["expiresAtUnix"] = service.repo.clock() - 1
    result = await service.run("codes")
    assert "StarterPack" not in text(result)
    assert "Premium required" in text(result)
    all_codes = await service.run("codes", show="all")
    assert "StarterPack — Expired" in text(all_codes)


async def test_stale_codes_are_not_claimed_active(service):
    await service.run("codes")
    service.repo.cache["/codes"].checked -= 1000
    service.repo.source.error = SourceError("offline")
    result = await service.run("codes")
    assert "Last seen; unverified" in text(result)
    assert "— Active" not in text(result)


async def test_old_market_data_stops_being_served(service):
    await service.run("price", "Holy Scroll")
    service.repo.cache["/market/holy-scroll"].checked -= 3601
    service.repo.source.error = SourceError("offline")
    with pytest.raises(SourceError):
        await service.run("price", "Holy Scroll")


async def test_missing_data_is_not_zero_or_absence_claim(service):
    result = await service.run("monster", "Acid Slime", "locations")
    assert "No information recorded" in result.embeds()[0].description


async def test_help_and_status_work_without_catalog(service):
    service.repo.catalog = service.repo.index = None
    assert "not ready" in text(await service.run("status"))
    assert "/help" in text(await service.run("help"))
    with pytest.raises(UserError, match="not ready"):
        await service.run("item", "Stick")
