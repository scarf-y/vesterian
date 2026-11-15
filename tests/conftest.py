import asyncio
import copy
import json
from pathlib import Path

import pytest

from vesterian.data import NotFound, Repository
from vesterian.service import Service

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def catalog():
    return json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture
def responses():
    return json.loads((FIXTURES / "responses.json").read_text(encoding="utf-8"))


class FakeSource:
    def __init__(self, catalog, responses):
        self.catalog, self.responses = catalog, responses
        self.calls = []
        self.error = None
        self.unchanged = False

    async def get(self, path, etag=None):
        self.calls.append((path, etag))
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        if path == "/api/catalog":
            return (None if self.unchanged else copy.deepcopy(self.catalog)), '"build"'
        if path not in self.responses:
            raise NotFound("Exact entry unavailable.")
        return copy.deepcopy(self.responses[path]), None


@pytest.fixture
async def service(tmp_path, catalog, responses):
    source = FakeSource(catalog, responses)
    repo = Repository(source, tmp_path, clock=lambda: 1794704400.0)
    await repo.refresh()
    yield Service(repo)
    await repo.close()
