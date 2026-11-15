"""Bounded source access and a persistent last-good catalog."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import aiohttp

from .core import ORIGIN, Index, UserError, page_url, route


class SourceError(UserError):
    pass


class NotFound(SourceError):
    pass


class PublicResolver(aiohttp.resolver.DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        records = await super().resolve(host, port, family)
        if any(not ipaddress.ip_address(r["host"]).is_global for r in records):
            raise OSError("Non-public destination blocked")
        return records


def retry_delay(value, now):
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(1.0, parsedate_to_datetime(value).timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            return 60.0


def decode(body):
    try:
        data = json.loads(body)

        def check(obj, depth=0):
            if depth > 64:
                raise ValueError("depth")
            if isinstance(obj, dict):
                for item in obj.values():
                    check(item, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    check(item, depth + 1)

        check(data)
        if not isinstance(data, dict):
            raise ValueError("object required")
        return data
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise SourceError("The database response format changed. Please try again later.") from exc


class HttpSource:
    def __init__(self, session, interval=1.0, clock=time.monotonic):
        self.session, self.interval, self.clock = session, interval, clock
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(2)
        self.next_start = self.paused_until = 0.0
        self.pending = 0
        self.failures = 0

    async def get(self, path, etag=None):
        path = route(path, format="json")
        if self.pending >= 50 or self.clock() < self.paused_until:
            raise SourceError("The database is busy. Please try again later.")
        self.pending += 1
        try:
            async with asyncio.timeout(5):
                await self.slots.acquire()
            try:
                async with asyncio.timeout(5):
                    async with self.lock:
                        await asyncio.sleep(max(0, self.next_start - self.clock()))
                        if self.clock() < self.paused_until:
                            raise SourceError("Database requests are temporarily paused.")
                        self.next_start = self.clock() + self.interval
                headers = {"If-None-Match": etag} if etag else {}
                cap = 16 * 1024 * 1024 if path.startswith("/api/catalog") else 2 * 1024 * 1024
                timeout = 30 if path.startswith("/api/catalog") else 8
                async with self.session.get(
                    ORIGIN + path,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 304:
                        return None, etag
                    if response.status == 404:
                        raise NotFound("That exact entry is no longer available from the database.")
                    if response.status == 429:
                        self.paused_until = self.clock() + retry_delay(
                            response.headers.get("Retry-After"), time.time()
                        )
                        raise SourceError("The database asked us to slow down. Please try again later.")
                    if response.status != 200:
                        raise SourceError("The database is temporarily unavailable.")
                    if response.content_type != "application/json":
                        raise SourceError("The database returned an unsupported response.")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > cap:
                            raise SourceError("The database response exceeded the size limit.")
                    data = await asyncio.to_thread(decode, body)
                    self.failures = 0
                    return data, response.headers.get("ETag")
            finally:
                self.slots.release()
        except NotFound:
            raise
        except (aiohttp.ClientError, OSError, TimeoutError, SourceError) as exc:
            self.failures += 1
            if self.failures >= 5:
                self.paused_until = max(self.paused_until, self.clock() + 60)
            if isinstance(exc, SourceError):
                raise
            raise SourceError("The database did not respond in time. Please try again later.") from exc
        finally:
            self.pending -= 1


def validate_payload(path, data):
    """Check the fields each command relies on before putting a response in cache."""
    if not isinstance(data, dict):
        raise SourceError("Invalid source data.")
    if path.startswith("/codes"):
        valid = all(isinstance(data.get(k), list) for k in ("codes", "expiredCodes"))
    elif path.startswith("/versions/"):
        valid = isinstance(data.get("changes"), list) and isinstance(data.get("version"), int)
    elif path.startswith("/market/"):
        valid = isinstance(data.get("markets"), list) and isinstance(data.get("name"), str)
    elif "/market-history" in path:
        valid = isinstance(data.get("days"), list)
    else:
        valid = isinstance(data.get("name"), str) and isinstance(data.get("url"), str)
        if valid:
            # Never accept a server silently falling back from a variant to its parent.
            valid = page_url(data["url"]) == page_url(path)
    if not valid:
        raise SourceError("The data format changed; this result cannot be verified.")


@dataclass
class Record:
    data: dict
    checked: float
    stale: bool = False
    size: int = 0
    generation: int = 0


class Repository:
    def __init__(self, source, directory: Path, clock=time.time):
        self.source, self.directory, self.clock = source, directory, clock
        self.catalog = self.index = None
        self.etag = None
        self.checked = 0.0
        self.refresh_error = False
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.inflight = {}
        self.refresh_lock = asyncio.Lock()
        self.generation = 0
        self.missing = OrderedDict()

    def load(self):
        try:
            saved = json.loads((self.directory / "catalog.json").read_text(encoding="utf-8"))
            index = Index(saved["data"])
            self.catalog, self.index = saved["data"], index
            self.etag, self.checked = saved.get("etag"), float(saved["checked"])
        except (OSError, ValueError, KeyError, UserError, TypeError):
            self.refresh_error = True

    async def refresh(self):
        async with self.refresh_lock:
            try:
                data, etag = await self.source.get("/api/catalog", self.etag if self.catalog else None)
                if data is None and self.catalog is None:
                    data, etag = await self.source.get("/api/catalog")
                data = data if data is not None else self.catalog
                candidate = await asyncio.to_thread(Index, data)
                if self.index:
                    for kind in ("item", "ability", "monster", "map"):
                        before = sum(e.kind == kind for e in self.index.entries.values())
                        after = sum(e.kind == kind for e in candidate.entries.values())
                        if after < before * 0.8:
                            raise SourceError("Catalog has an unexpected removal; keeping previous data.")
                checked = self.clock()
                saved = {"data": data, "etag": etag, "checked": checked}
                await asyncio.to_thread(self._save, saved)
                if data != self.catalog:
                    self.generation += 1
                    self.missing.clear()
                self.catalog, self.index, self.etag, self.checked = data, candidate, etag, checked
                self.refresh_error = False
            except (UserError, KeyError, TypeError, OSError, ValueError) as exc:
                self.refresh_error = True
                raise SourceError("Catalog refresh failed; previous data retained.") from exc

    def _save(self, saved):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.directory / "catalog.tmp"
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        os.replace(tmp, self.directory / "catalog.json")

    def ready_index(self):
        if self.index is None or self.clock() - self.checked > 7 * 86400:
            raise UserError("The catalog is not ready. Try /status and come back shortly.")
        return self.index

    async def get(self, path, fresh=21600, maximum=604800):
        path = route(path)
        if self.missing.get(path, 0) > self.clock():
            raise NotFound("That exact entry is currently unavailable. Try again shortly.")
        old = self.cache.get(path)
        if old and old.generation == self.generation and self.clock() - old.checked < fresh:
            self.cache.move_to_end(path)
            return old
        if path not in self.inflight:
            if len(self.inflight) >= 50:
                raise SourceError("Too many data requests are in progress. Try again shortly.")
            task = asyncio.create_task(self._fetch(path))
            self.inflight[path] = task

            def completed(done):
                self.inflight.pop(path, None)
                if not done.cancelled():
                    done.exception()  # Consume errors even if all waiting commands timed out.

            task.add_done_callback(completed)
        try:
            return await asyncio.shield(self.inflight[path])
        except NotFound:
            self.cache_bytes -= self.cache.pop(path).size if path in self.cache else 0
            self.missing[path] = self.clock() + 60
            self.missing.move_to_end(path)
            while len(self.missing) > 5000:
                self.missing.popitem(last=False)
            raise
        except SourceError:
            if old and self.clock() - old.checked <= maximum:
                return Record(old.data, old.checked, True, old.size)
            raise

    async def _fetch(self, path):
        generation = self.generation
        data, _ = await self.source.get(path)
        validate_payload(path, data)
        record = Record(
            data,
            self.clock(),
            size=len(json.dumps(data)),
            generation=generation,
            stale=generation != self.generation,
        )
        if generation == self.generation:
            self.cache_bytes -= self.cache[path].size if path in self.cache else 0
            self.cache[path] = record
            self.cache.move_to_end(path)
            self.cache_bytes += record.size
            while len(self.cache) > 5000 or self.cache_bytes > 256 * 1024 * 1024:
                _, expired = self.cache.popitem(last=False)
                self.cache_bytes -= expired.size
        return record

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
