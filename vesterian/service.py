"""Command behavior, independent of Discord connections."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from .core import Result, UserError, clean, display, label, linked, page_url, route
from .presentation import color, media_url, money
from .charts import ChartRenderer, RANGES, METRICS, series, volume_rows, count
import math
from .cards import compact

VIEWS = {
    "item": ("overview", "sources", "crafting", "details"),
    "ability": ("overview", "ranks", "details"),
    "monster": ("overview", "drops", "locations", "details"),
    "map": ("overview", "monsters", "npcs", "exits"),
    "price": ("summary", "chart", "history"),
}
HELP = {
    "search": "query:<name> [type:<item/ability/monster/map/updates>] — choose an exact match.",
    "item": "name:<item> [view:overview/sources/crafting/details] — stats, acquisition, recipes and reforging.",
    "ability": "name:<skill> [view:overview/ranks/details] — mechanics, requirements and separate rank paths.",
    "monster": "name:<monster> [view:overview/drops/locations/details] — stats, conditional loot and known spawns.",
    "map": "name:<map> [view:overview/monsters/npcs/exits] — inhabitants and connections.",
    "price": "item:<item> [view:summary/chart/history] — sales, asking prices and daily history in game currency.",
    "codes": "[show:active/all] — rewards, requirements and expiration; no redemption through the bot.",
    "updates": "[version:<number>] — latest release or a release containing that number.",
    "help": "[command:<command>] — command examples and available options.",
    "status": "— catalog readiness, source check time and build date.",
}


def timestamp(value):
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")


def sections(data, keys):
    return [f"**{clean(label(k))}**\n{display(data[k])}" for k in keys if data.get(k) is not None]


class Service:
    def __init__(self, repo):
        self.repo = repo
        self.charts = ChartRenderer()

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
            return await self.price(
                entry, view, options.get("window", "7d"), options.get("metric", "averagePrice")
            )
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
                    f"**{linked(drop)}**\n{clean(drop.get('chanceText', 'unknown'))} per attempt · "
                    f"Quantity: {clean(drop.get('amountText') or 'not specified')} · "
                    f"Attempts: {clean(drop.get('rollCountText') or 'not specified')}\n"
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
        result = self.result(f"{entry.name} · {view.capitalize()}", lines, entry.path, record)
        if view == "overview":
            compact(result, command, d)
            if record.stale:
                result.lines.insert(0, "⚠ **Showing saved data — the source could not be refreshed.**")
        if command == "monster" and view == "locations" and not lines:
            result.lines = [
                "No information recorded for spawn locations. Open the database page or try another encounter."
            ]
        if command == "monster" and view == "drops" and not lines:
            result.lines = [
                "No drops recorded for this encounter. Try another variant or open the database page."
            ]
        return result

    def result(self, title, lines, path, record):
        note = f"{'Cached; refresh unavailable. ' if record.stale else ''}Checked {timestamp(record.checked)}"
        result = Result(title, lines, page_url(path), note)
        if record.stale:
            result.lines = ["⚠ **Showing saved data — the source could not be refreshed.**"] + result.lines
        d = record.data
        tier = d.get("tier")
        palette = (self.repo.catalog or {}).get("constants", {}).get("tierColors", {})
        result.color = (
            color(palette.get(str(tier))) if path.startswith("/items/") and tier is not None else 0x66B99A
        )
        if tier is not None:
            result.badge = f"**Tier {clean(tier)}**" + (
                f" · {clean(d['itemType'])}" if d.get("itemType") else ""
            )
        if d.get("hasImage") is not False:
            result.thumbnail = media_url(d.get("thumbnailUrl"))
        if d.get("assetType") == "map":
            result.image = media_url(d.get("imageUrl"))
            result.thumbnail = media_url(d.get("keyArtThumbnailUrl"))
        return result

    async def price(self, entry, view, window="7d", metric="averagePrice"):
        if window not in RANGES or metric not in METRICS:
            raise UserError("Choose an available chart range and metric.")
        p = urlsplit(entry.path)
        historical = view in {"history", "chart"}
        path = p.path + "/market-history" if historical else p.path.replace("/items/", "/market/", 1)
        if p.query:
            path += "?" + p.query
        record = await self.repo.get(
            route(path),
            fresh=3600 if historical else 900,
            maximum=21600 if historical else 3600,
        )
        d = record.data
        if view == "chart":
            result = self.result(
                f"{entry.name} — price chart",
                [
                    f"**{METRICS[metric]} · {window}**",
                    "Change the range or metric below. Only comparable per-item prices are plotted.",
                ],
                entry.path,
                record,
            )
            result.chart = await self.charts.draw(entry.name, d, window, metric, self.repo.clock())
            result.chart_range, result.chart_metric = window, metric
            points, _, _ = series(d, window, metric, self.repo.clock())
            observed = [(t, v) for t, v in points if math.isfinite(v)]
            rows = volume_rows(d, window, self.repo.clock())
            if observed:
                first, last = observed[0], observed[-1]
                change = (
                    f"{(last[1] / first[1] - 1) * 100:+.1f}%"
                    if len(observed) > 1 and first[1]
                    else "Not comparable"
                )
                result.fields = [
                    ("Latest recorded", money(last[1]), True),
                    ("Change", change, True),
                    ("Items sold", f"{sum(count(r.get('units')) for _, r in rows):,}", True),
                    ("Transactions", f"{sum(count(r.get('purchases')) for _, r in rows):,}", True),
                ]
                result.lines = [
                    f"**{METRICS[metric]} · {window}**",
                    f"Price comparison: {first[0]:%d %b %H:%M} → {last[0]:%d %b %H:%M} UTC.",
                    "Volume counts cover available non-legacy buckets in the selected range.",
                ]
                if record.stale:
                    result.lines.insert(0, "⚠ **Showing saved data — the source could not be refreshed.**")
            else:
                result.lines.append("No comparable prices recorded. Try **30d** or a different metric.")
            result.thumbnail = media_url(p.path + "/thumbnail" + ("?" + p.query if p.query else ""))
            return result
        if view == "history":
            lines = [
                f"**{clean(day['day'])} {'— legacy per-sale pricing' if day.get('legacy') else '— per-item pricing'}**\n"
                f"Average sale: {money(day.get('averagePrice'))}; median: {money(day.get('medianPrice'))}\n"
                f"Transactions: {day.get('purchases', '?')}; items sold: {day.get('units', '?') if not day.get('legacy') else 'not tracked'}"
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
        lines.append(
            "1 Platinum = 1,000 Gold = 1,000,000 Silver = 1,000,000,000 Bronze. Sales and asks are different measures."
        )
        result = self.result(f"{entry.name} — market {view}", lines, entry.path, record)
        if view == "summary":
            result.lines = [
                "Completed sales and asking prices are different measures. Choose **Chart** to explore trends."
            ]
            for r in rows:
                window_name = clean(r.get("window", "Market"))
                result.fields.extend(
                    [
                        (window_name + " · Average sale", money(r.get("averagePrice")), True),
                        (window_name + " · Median sale", money(r.get("medianPrice")), True),
                        (window_name + " · Average ask", money(r.get("averageAskingPrice")), True),
                        (
                            window_name + " · Activity",
                            f"{count(r.get('purchases')):,} transactions · {count(r.get('units')):,} items sold",
                            False,
                        ),
                    ]
                )
            if not rows:
                result.lines = ["No market activity recorded. Try **History** or choose another item."]
            if record.stale:
                result.lines.insert(0, "⚠ **Showing saved data — the source could not be refreshed.**")
            result.note += " · Source updated: " + clean(d.get("updatedAt", "unknown"))
        result.thumbnail = media_url(p.path + "/thumbnail" + ("?" + p.query if p.query else ""))
        return result

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
