import asyncio
import copy
import json

import pytest

from vesterian.core import Index, Limits, Result, UserError, clean, route
from vesterian.data import NotFound, Repository, SourceError, decode, retry_delay


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/items/stick",
        "//db.playvesteria.com/items/stick",
        "http://db.playvesteria.com/items/stick",
        "https://user@db.playvesteria.com/items/stick",
        "/items/../login",
        "/items/%2e%2e/login",
        "/items/stick?url=evil",
        "/items/stick?variation=a&variation=b",
        "/items/stick?variation=a%26format=html",
        "/items/stick\\foo",
        "file:///items/stick",
        "/login",
    ],
)
def test_url_boundary(url):
    with pytest.raises(UserError):
        route(url)


def test_variant_preserved():
    assert (
        route("/items/holy-scroll?variation=magnificent", format="json")
        == "/items/holy-scroll?format=json&variation=magnificent"
    )


def test_hidden_variants_not_in_autocomplete(catalog):
    index = Index(catalog)
    assert not index.search("Bone Throw")
    assert index.search("Boulder Throw")[0].path.endswith("variation=boulder-throw")


def test_rendering_bounds_and_mentions():
    assert "@everyone" not in clean("<b>@everyone</b> **hi**")
    pages = Result("x" * 300, ["x" * 8000 for _ in range(20)]).embeds()
    assert len(pages) > 1
    assert all(len(e) <= 6000 and len(e.description) <= 4096 and len(e.title) <= 256 for e in pages)


def test_atomic_limits_and_refill():
    now = [0]
    limits = Limits(clock=lambda: now[0])
    for _ in range(5):
        limits.take(1, 1)
    before = copy.deepcopy(limits.buckets)
    with pytest.raises(UserError, match="3 seconds"):
        limits.take(1, 2)
    assert limits.buckets == before  # failed admission charges no other bucket
    limits.take(2, 2)
    now[0] = 3
    limits.take(1, 2)


async def test_catalog_304_and_restart(service, tmp_path):
    repo = service.repo
    built = repo.catalog["build"]["builtAt"]
    repo.source.unchanged = True
    repo.clock = lambda: 1794704500.0
    await repo.refresh()
    assert repo.source.calls[-1][1] == '"build"'
    restored = Repository(repo.source, tmp_path, clock=repo.clock)
    restored.load()
    assert restored.checked == 1794704500.0
    assert restored.catalog["build"]["builtAt"] == built
    assert restored.index.search("Stick")


async def test_invalid_or_mass_removal_keeps_previous_catalog(service):
    previous = service.repo.catalog
    service.repo.source.catalog["version"] = 999
    with pytest.raises(SourceError):
        await service.repo.refresh()
    assert service.repo.catalog is previous
    service.repo.source.catalog["version"] = 1
    service.repo.source.catalog["assets"]["items"] = []
    with pytest.raises(SourceError):
        await service.repo.refresh()
    assert service.repo.catalog is previous


async def test_same_key_misses_share_a_request(service):
    results = await asyncio.gather(*(service.repo.get("/items/stick") for _ in range(15)))
    assert all(r.data["name"] == "Stick" for r in results)
    assert sum(p == "/items/stick" for p, _ in service.repo.source.calls) == 1


async def test_failed_fetch_serves_bounded_stale_and_404_does_not(service):
    repo = service.repo
    await repo.get("/items/stick")
    repo.cache["/items/stick"].checked -= 22000
    repo.source.error = SourceError("offline")
    assert (await repo.get("/items/stick")).stale
    repo.source.error = NotFound("not found")
    with pytest.raises(NotFound):
        await repo.get("/items/stick")
    assert "/items/stick" not in repo.cache


async def test_silent_variant_fallback_rejected(service):
    path = "/items/holy-scroll?variation=magnificent"
    service.repo.source.responses[path]["url"] = "/items/holy-scroll"
    with pytest.raises(SourceError):
        await service.repo.get(path)


def test_json_and_retry_parsing():
    for body in [b"<html>bad</html>", b"[]", json.dumps({"a": "["}).encode(), b"{bad"]:
        if body == json.dumps({"a": "["}).encode():
            assert decode(body)["a"] == "["
        else:
            with pytest.raises(SourceError):
                decode(body)
    assert retry_delay("120", 0) == 120
    assert retry_delay("Thu, 01 Jan 1970 00:02:00 GMT", 60) == 60
    assert retry_delay("bad", 0) == 60
    with pytest.raises(SourceError):
        decode(b'{"deep":' + b"[" * 70 + b"0" + b"]" * 70 + b"}")


async def test_catalog_change_revalidates_details_and_keeps_stale_fallback(service):
    repo = service.repo
    first = await repo.get("/items/stick")
    repo.source.catalog["build"]["id"] = "next-build"
    await repo.refresh()
    assert repo.generation != first.generation
    repo.source.error = SourceError("offline")
    result = await repo.get("/items/stick")
    assert result.stale and result.checked == first.checked


async def test_negative_cache_limits_missing_entry_requests(service):
    repo = service.repo
    for _ in range(3):
        with pytest.raises(NotFound):
            await repo.get("/items/missing")
    assert sum(path == "/items/missing" for path, _ in repo.source.calls) == 1


async def test_cancelled_waiter_does_not_cancel_shared_fetch(service):
    repo = service.repo
    original = repo.source.get
    started, proceed = asyncio.Event(), asyncio.Event()

    async def slow(path, etag=None):
        started.set()
        await proceed.wait()
        return await original(path, etag)

    repo.source.get = slow
    first = asyncio.create_task(repo.get("/items/stick"))
    await started.wait()
    second = asyncio.create_task(repo.get("/items/stick"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    proceed.set()
    assert (await second).data["name"] == "Stick"
    assert sum(path == "/items/stick" for path, _ in repo.source.calls) == 1
