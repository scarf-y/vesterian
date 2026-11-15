"""Command behavior, independent of Discord connections."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from .core import Result, UserError, clean, display, label, linked, page_url, route

VIEWS = {
    "item": ("overview", "sources", "crafting"),
    "ability": ("overview", "ranks"),
    "monster": ("overview", "drops", "locations"),
    "map": ("overview", "monsters", "npcs", "exits"),
    "price": ("summary", "history"),
}
HELP = {
    "search": "query:<name> [type:<item/ability/monster/map/updates>] — choose an exact match.",
    "item": "name:<item> [view:overview/sources/crafting] — stats, acquisition, recipes and reforging.",
    "ability": "name:<skill> [view:overview/ranks] — mechanics, requirements and separate rank paths.",
    "monster": "name:<monster> [view:overview/drops/locations] — stats, conditional loot and known spawns.",
    "map": "name:<map> [view:overview/monsters/npcs/exits] — inhabitants and connections.",
    "price": "item:<item> [view:summary/history] — sales, asking prices and daily history in Mushcoins.",
    "codes": "[show:active/all] — rewards, requirements and expiration; no redemption through the bot.",
    "updates": "[version:<number>] — latest release or a release containing that number.",
    "help": "[command:<command>] — command examples and available options.",
    "status": "— catalog readiness, source check time and build date.",
}


def timestamp(value):
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")


def money(value):
    return f"{value:,.2f} Mushcoins" if isinstance(value, (int, float)) else "Not recorded"


def sections(data, keys):
    return [f"**{clean(label(k))}**\n{display(data[k])}" for k in keys if data.get(k) is not None]


class Service:
    def __init__(self, repo):
        self.repo = repo

    async def run(self, command, query="", view=None, **options):
        if command not in HELP:
            raise UserError("Unknown command. Use /help.")
        if command == "help":
            if query and query not in HELP:
                raise UserError("Unknown command. Use /help without an option to list commands.")
            return Result(
                "Vesterian help",
                [f"**/{k}** {v}" for k, v in HELP.items() if not query or k == query]
                + ["Replies are private unless you choose public:true. Controls expire after 10 minutes."],
            )
        if command == "status":
            return Result(
                "Vesterian status",
                [
                    f"Catalog: {'ready' if self.repo.index and self.repo.clock() - self.repo.checked <= 604800 else 'not ready'}",
                    f"Last validated: {timestamp(self.repo.checked) if self.repo.checked else 'never'}",
                    f"Source build: {clean((self.repo.catalog or {}).get('build', {}).get('builtAt', 'unknown'))}",
                    f"Last refresh: {'failed; using saved data where allowed' if self.repo.refresh_error else 'successful'}",
                    "Detail responses are fetched separately. Website data may differ from the live game.",
                ],
            )
        if command == "codes":
            return await self.codes(options.get("show", "active"))
        index = self.repo.ready_index()
        if command == "updates":
            rows = self.repo.catalog.get("versions", [])
            if query:
                if not str(query).isdigit():
                    raise UserError("Use a release number, for example 1359.")
                rows = [
                    r
                    for r in rows
                    if r.get("version") == int(query)
                    or any(v.get("version") == int(query) for v in r.get("includedVersions", []))
                ]
            if not rows:
                raise UserError("That release is not in the current catalog.")
            selected = max(rows, key=lambda r: r.get("lastVersion", r["version"]))
            record = await self.repo.get(selected["url"], fresh=900)
            d = record.data
            lines = sections(d, ["summary", "notes", "footnotes"])
            lines += [
                f"**{linked(c)} — {clean(c.get('changeType', 'Change'))}**\n"
                f"{display(c.get('summary'))}\n{display(c.get('details', []))}"
                for c in d.get("changes", [])
            ]
            return self.result(d.get("versionLabel", str(d["version"])), lines, selected["url"], record)
        if not query.strip():
            raise UserError("Enter a name or select an autocomplete suggestion.")
        kind = options.get("type") if command == "search" else ("item" if command == "price" else command)
        if kind and kind not in {"item", "ability", "monster", "map", "updates"}:
            raise UserError("Choose a supported search category.")
        if command in VIEWS:
            view = view or VIEWS[command][0]
            if view not in VIEWS[command]:
                raise UserError("Choose one of the available views.")
        matches = index.search(query, kind)
        if not matches:
            raise UserError("No matching entry in the current catalog. Try a shorter name or /search.")
        exact = [e for e in matches if e.name.casefold() == query.strip().casefold() or e.key == query]
        if command == "search" or (len(matches) > 1 and len(exact) != 1):
            return Result(
                "Choose an entry",
                [f"**{clean(e.name)}** — {e.kind}; {clean(e.context)}" for e in matches],
                choices=matches,
                note="Up to 25 matches. Narrow your query if needed.",
            )
        entry = exact[0] if len(exact) == 1 else matches[0]
        if command == "price":
            return await self.price(entry, view)
        record = await self.repo.get(entry.path)
        d = record.data
        if d.get("hidden") or d.get("groupOnly"):
            raise UserError("That entry is not a public encounter or available asset.")
        if command == "item":
            metadata = d.get("metadata", {})
            if view == "sources":
                lines = [display(metadata.get("obtainedFrom"))]
            elif view == "crafting":
                lines = []
                for key in ("craftingRecipes", "reforgeRecipes", "usedInRecipes"):
                    lines.append(f"**{label(key)}**")
                    for recipe in d.get(key, []):
                        lines.append(
                            f"Ingredients: {display(recipe.get('ingredients', []))}\n"
                            f"Results: {display(recipe.get('results', []))}"
                        )
                    if not d.get(key):
                        lines.append("None recorded.")
            else:
                lines = sections(
                    d, ["description", "tier", "itemType", "requiredClasses", "soulbound", "behaviorSummary"]
                )
                lines += sections(metadata, [k for k in metadata if k != "obtainedFrom"])
                lines += sections(d, ["grantsAbilities"])
        elif command == "ability":
            if view == "ranks":
                lines = [
                    f"**{clean(r.get('name') or 'Rank progression')}**\n{display(r.get('table'))}"
                    for r in d.get("rankTables", [])
                ]
            else:
                lines = sections(
                    d,
                    [
                        "description",
                        "requirementText",
                        "stats",
                        "behaviorNotes",
                        "differences",
                        "interactions",
                        "grantedBy",
                    ],
                )
        elif command == "monster":
            if view == "drops":
                lines = sections(d, ["dropNote"]) + [
                    f"**{linked(drop)}**\nChance per attempt: {clean(drop.get('chanceText', 'unknown'))}; "
                    f"amount per success: {clean(drop.get('amountText') or 'not specified')}; "
                    f"attempts: {clean(drop.get('rollCountText') or 'not specified')}\n"
                    f"{display(drop.get('condition') or '')} {display(drop.get('note') or '')}"
                    for drop in d.get("drops", [])
                ]
            elif view == "locations":
                lines = [
                    f"{linked(s)} — {s.get('regions', '?')} spawn regions"
                    for s in d.get("spawnLocations", [])
                ]
            else:
                lines = sections(d, ["description", "bookStats", "combatStats", "behaviorNotes", "bounties"])
        else:  # map
            if view == "overview":
                lines = sections(d, ["tags", "counts", "calibrated", "capturedAt"])
            elif view == "npcs":
                lines = [
                    f"**{clean(n['name'])}** — {display(n.get('roles', []))}"
                    + (f"; shop: {clean(n['shop'])}" if n.get("shop") else "")
                    for n in d.get("characters", [])
                ]
            else:
                lines = [
                    linked(n) + (" — destination has no map page" if not n.get("url") else "")
                    for n in d.get(view, [])
                ]
        return self.result(f"{entry.name} — {view}", lines, entry.path, record)

    def result(self, title, lines, path, record):
        note = f"{'Cached; refresh unavailable. ' if record.stale else ''}Checked {timestamp(record.checked)}"
        return Result(title, lines, page_url(path), note)

    async def price(self, entry, view):
        p = urlsplit(entry.path)
        path = p.path + "/market-history" if view == "history" else p.path.replace("/items/", "/market/", 1)
        if p.query:
            path += "?" + p.query
        record = await self.repo.get(
            route(path),
            fresh=3600 if view == "history" else 900,
            maximum=21600 if view == "history" else 3600,
        )
        d = record.data
        if view == "history":
            lines = [
                f"**{clean(day['day'])} {'— legacy per-sale pricing' if day.get('legacy') else '— per-item pricing'}**\n"
                f"Average sale: {money(day.get('averagePrice'))}; median: {money(day.get('medianPrice'))}\n"
                f"Purchases: {day.get('purchases', '?')}; units: {day.get('units', '?')}"
                for day in sorted(d.get("days", []), key=lambda d: d["day"], reverse=True)
            ]
        else:
            rows = ([d["last24Hours"]] if d.get("last24Hours") else []) + d.get("markets", [])
            lines = [
                f"**{clean(r.get('window', 'Market'))}**\nAverage sale: {money(r.get('averagePrice'))}\n"
                f"Median sale: {money(r.get('medianPrice'))}\nAverage asking price: {money(r.get('averageAskingPrice'))}\n"
                f"Purchases: {r.get('purchases', '?')}; units sold: {r.get('units', '?')}"
                for r in rows
            ]
            lines.append(f"Source updated: {clean(d.get('updatedAt', 'not supplied'))}")
        lines.append("1 Gold = 1,000,000 Mushcoins. Sales and asking prices are different measures.")
        return self.result(f"{entry.name} — market {view}", lines, entry.path, record)

    async def codes(self, show):
        if show not in {"active", "all"}:
            raise UserError("Choose active or all codes.")
        record = await self.repo.get("/codes", fresh=900, maximum=3600)
        now, lines = self.repo.clock(), []
        for c in record.data["codes"] + record.data["expiredCodes"]:
            expired = c.get("expired", False) or (
                c.get("expiresAtUnix") is not None and c["expiresAtUnix"] <= now
            )
            future = c.get("startsAtUnix") is not None and c["startsAtUnix"] > now
            if show == "active" and (expired or future):
                continue
            state = (
                "Expired"
                if expired
                else ("Upcoming" if future else ("Last seen; unverified" if record.stale else "Active"))
            )
            requirements = c.get("requirements", {})
            reqs = []
            if requirements.get("level"):
                reqs.append(f"Level {requirements['level']}")
            if requirements.get("premium"):
                reqs.append("Premium required")
            reqs += [clean(r.get("label", "Additional requirement")) for r in requirements.get("other", [])]
            lines.append(
                f"**{clean(c['code'])} — {state}**\nRewards: {display(c.get('effects', []))}\n"
                f"Requirements: {', '.join(reqs) or 'None recorded'}\n"
                f"Expires: {clean(c.get('expiresAt') or 'not specified')}"
            )
        return self.result("Vesteria codes", lines, "/codes", record)
