import json
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from vesterian.data import HttpSource, NotFound, PublicResolver, SourceError


class Response:
    def __init__(self, status=200, data=None, headers=None, content_type="application/json", body=None):
        self.status, self.headers, self.content_type = status, headers or {}, content_type
        self.body = body if body is not None else json.dumps(data or {"name": "Stick"}).encode()
        self.content = self

    async def iter_chunked(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i : i + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


async def test_fetch_forces_json_disables_redirect_and_preserves_variant():
    session = Session(Response())
    await HttpSource(session, interval=0).get("/items/holy-scroll?variation=magnificent")
    url, opts = session.calls[0]
    assert url.endswith("?format=json&variation=magnificent")
    assert opts["allow_redirects"] is False
    assert "Authorization" not in opts["headers"]


@pytest.mark.parametrize(
    "response",
    [
        Response(302),
        Response(500),
        Response(content_type="text/html"),
        Response(body=b"x" * (2 * 1024 * 1024 + 1)),
    ],
)
async def test_redirects_errors_html_and_oversized_responses_rejected(response):
    source = HttpSource(Session(response), interval=0)
    with pytest.raises(SourceError):
        await source.get("/items/stick")
    assert source.pending == 0
    assert source.slots._value == 2


async def test_retry_after_pauses_all_upstream_work():
    session = Session(Response(429, headers={"Retry-After": "120"}))
    source = HttpSource(session, interval=0, clock=lambda: 10)
    with pytest.raises(SourceError):
        await source.get("/items/stick")
    with pytest.raises(SourceError):
        await source.get("/codes")
    assert source.paused_until == 130
    assert len(session.calls) == 1


async def test_304_uses_etag_without_body():
    session = Session(Response(304, body=b""))
    assert await HttpSource(session, interval=0).get("/api/catalog", '"one"') == (None, '"one"')
    assert session.calls[0][1]["headers"]["If-None-Match"] == '"one"'


async def test_404_is_distinct():
    with pytest.raises(NotFound):
        await HttpSource(Session(Response(404)), interval=0).get("/items/stick")


async def test_private_dns_address_is_blocked():
    with patch.object(
        aiohttp.resolver.DefaultResolver, "resolve", AsyncMock(return_value=[{"host": "127.0.0.1"}])
    ):
        resolver = PublicResolver()
        try:
            with pytest.raises(OSError):
                await resolver.resolve("db.playvesteria.com", 443)
        finally:
            await resolver.close()
