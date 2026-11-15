"""Validated source identities, search, presentation, and admission control."""

from __future__ import annotations

import hashlib
import html
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

import discord

ORIGIN = "https://db.playvesteria.com"
ROUTE = re.compile(
    r"/(?:api/catalog|codes|versions(?:/\d+)?|items/[a-z0-9-]+(?:/market-history)?|"
    r"abilities/[a-z0-9-]+(?:/[a-z0-9-]+)?|monsters/[a-z0-9-]+|"
    r"maps/[a-z0-9-]+|market/[a-z0-9-]+)"
)


class UserError(Exception):
    """A deliberately safe, user-visible error."""


def route(value: str, **params) -> str:
    """Normalize only reviewed read routes; never accept arbitrary fetch targets."""
    if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in "\\\r\n\t%#"):
        raise UserError("The database supplied an unsupported link.")
    p = urlsplit(value)
    if p.netloc and (p.scheme != "https" or p.netloc != "db.playvesteria.com"):
        raise UserError("External data links are not supported.")
    if p.scheme and not p.netloc or value.startswith("//") or not ROUTE.fullmatch(p.path):
        raise UserError("The database supplied an unsupported route.")
    pairs = parse_qsl(p.query, keep_blank_values=True)
    q = dict(pairs)
    if len(q) != len(pairs) or set(q) - {"variation", "format", "attribute"}:
        raise UserError("Unsupported data parameters.")
    q.update({k: str(v) for k, v in params.items() if v is not None})
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", v) for v in q.values()):
        raise UserError("Unsupported data parameter value.")
    return p.path + ("?" + urlencode(sorted(q.items())) if q else "")


def page_url(value: str) -> str:
    p = urlsplit(route(value))
    q = [(k, v) for k, v in parse_qsl(p.query) if k != "format"]
    return ORIGIN + p.path + ("?" + urlencode(q) if q else "")


def clean(value, limit=1000) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", "", str(value)))
    text = "".join(c for c in text if c in "\n\t" or c.isprintable())
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]


def label(key):
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").capitalize()


def linked(value: dict) -> str:
    name = clean(value.get("text") or value.get("name") or value.get("label") or "Entry", 180)
    try:
        return f"[{name}]({page_url(value['url'])})" if value.get("url") else name
    except UserError:
        return name


def display(value, depth=0) -> str:
    """Render source display primitives without losing table column labels."""
    if depth > 8 or value is None:
        return "Not recorded"
    if isinstance(value, (str, int, float, bool)):
        return clean(value)
    if isinstance(value, list):
        return "\n".join(display(v, depth + 1) for v in value[:100]) or "None recorded"
    if not isinstance(value, dict):
        return "Not recorded"
    if value.get("kind") == "table":
        cols = value.get("columns", [])
        return "\n".join(
            " | ".join(
                f"{display(cols[i], depth + 1)}: {display(cell, depth + 1)}"
                if i < len(cols)
                else display(cell, depth + 1)
                for i, cell in enumerate(row)
            )
            for row in value.get("rows", [])[:100]
        )
    if value.get("kind") == "stats":
        return "\n".join(
            f"{clean(s.get('label', 'Stat'))}: {display(s.get('value'), depth + 1)}"
            for s in value.get("stats", [])[:100]
        )
    if value.get("kind") == "list":
        return display(value.get("entries", []), depth + 1)
    if value.get("kind") == "items":
        return display(value.get("items", []), depth + 1)
    if value.get("kind") == "currency":
        return f"{clean(value.get('amount', '?'))} {clean(value.get('currency', 'currency'))}"
    if "text" in value:
        return linked(value) + (f" {clean(value['name'])}" if "rawValue" in value else "")
    if "name" in value and "url" in value:
        count = value.get("quantity", value.get("stacks"))
        return linked(value) + (f" × {count}" if count is not None else "")
    return "\n".join(
        f"{clean(label(k))}: {display(v, depth + 1)}"
        for k, v in value.items()
        if v is not None and k not in {"kind", "url", "thumbnailUrl", "iconUrl"}
    )


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    path: str
    context: str = ""

    @property
    def key(self):
        return "id:" + hashlib.sha256(self.path.encode()).hexdigest()[:24]


class Index:
    def __init__(self, catalog):
        if not isinstance(catalog, dict) or not isinstance(catalog.get("build"), dict):
            raise UserError("Invalid catalog structure; keeping previous data.")
        if catalog.get("version") != 1 or not isinstance(catalog.get("build", {}).get("id"), str):
            raise UserError("Unsupported catalog format; keeping previous data.")
        self.entries = {}
        assets = catalog.get("assets", {})
        if not isinstance(assets, dict):
            raise UserError("Invalid catalog assets; keeping previous data.")
        for family in ("items", "abilities", "monsters", "maps"):
            if not isinstance(assets.get(family), list):
                raise UserError("Incomplete catalog; keeping previous data.")

        def add(row, kind, context=""):
            if not isinstance(row, dict):
                raise UserError("Invalid catalog entry; keeping previous data.")
            if row.get("hidden") or row.get("groupOnly"):
                return
            name = row.get("name") or row.get("versionLabel")
            if not isinstance(name, str) or not name:
                raise UserError("A catalog entry has no name.")
            entry = Entry(name, kind, route(row["url"]), context)
            if entry.key in self.entries:
                raise UserError("Duplicate catalog identity.")
            self.entries[entry.key] = entry

        for family, kind in [("items", "item"), ("monsters", "monster"), ("maps", "map")]:
            for row in assets[family]:
                add(row, kind)
                for variant in row.get("variations", []):
                    add(variant, kind, row["name"] + " variant")
        for book in assets["abilities"]:
            if not isinstance(book, dict):
                raise UserError("Invalid ability book; keeping previous data.")
            if book.get("hidden"):
                continue
            for ability in book.get("abilities", []):
                add(ability, "ability", book["name"])
                for variant in ability.get("variations", []):
                    add(variant, "ability", book["name"] + " / " + ability["name"])
        for version in catalog.get("versions", []):
            add(version, "updates")

    def search(self, query, kind=None, limit=25):
        if not isinstance(query, str) or len(query) > 100:
            raise UserError("Use a name of at most 100 characters.")
        query = query.strip().casefold()
        if query.startswith("id:"):
            item = self.entries.get(query)
            return [item] if item and (not kind or item.kind == kind) else []
        candidates = [
            e
            for e in self.entries.values()
            if (not kind or e.kind == kind)
            and all(word in (e.name + " " + e.context).casefold() for word in query.split())
        ]
        return sorted(
            candidates,
            key=lambda e: (
                e.name.casefold() != query,
                not e.name.casefold().startswith(query),
                e.name,
                e.path,
            ),
        )[:limit]


@dataclass
class Result:
    title: str
    lines: list[str]
    url: str | None = None
    note: str = ""
    choices: list[Entry] = field(default_factory=list)

    def embeds(self):
        chunks, current = [], []
        for line in self.lines or ["No information recorded in the database."]:
            for part in [line[i : i + 900] for i in range(0, len(line), 900)] or [""]:
                if len(current) >= 10 or sum(map(len, current)) + len(part) > 3500:
                    chunks.append("\n\n".join(current))
                    current = []
                current.append(part)
        if current:
            chunks.append("\n\n".join(current))
        embeds = []
        for i, chunk in enumerate(chunks[:100]):
            embed = discord.Embed(
                title=clean(self.title, 240), description=chunk, url=self.url, color=0x66B99A
            )
            embed.set_footer(text=f"{self.note} | Page {i + 1}/{min(len(chunks), 100)}"[:1900])
            embeds.append(embed)
        return embeds


class Limits:
    """Atomic on a single asyncio event loop: no await between check and spend."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.buckets = clock, OrderedDict()

    def take(self, user, guild, cost=1, autocomplete=False):
        specs = (
            [(("auto", user), 10, 5), (("auto-global", 0), 100, 50)]
            if autocomplete
            else [(("user", user), 5, 20 / 60), (("guild", guild), 20, 2), (("app", 0), 40, 10)]
        )
        now, balances, wait = self.clock(), [], 0
        for key, cap, rate in specs:
            tokens, at = self.buckets.get(key, (cap, now))
            tokens = min(cap, tokens + max(0, now - at) * rate)
            balances.append((key, tokens))
            wait = max(wait, (cost - tokens) / rate)
        if wait > 0:
            raise UserError(f"Please wait {math.ceil(wait)} seconds before trying again.")
        for key, tokens in balances:
            self.buckets[key] = (tokens - cost, now)
            self.buckets.move_to_end(key)
        while len(self.buckets) > 100000:
            self.buckets.popitem(last=False)
