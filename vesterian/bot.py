"""Discord Gateway transport. Running requires DISCORD_TOKEN in the environment."""

from __future__ import annotations

import argparse
import io
import asyncio
import logging
import os
import random
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Literal

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from .core import Limits, UserError
from .data import HttpSource, PublicResolver, Repository, SourceError
from .service import VIEWS, Service
from .charts import RANGES, METRICS
from .presentation import ICONS, DENOMINATIONS

log = logging.getLogger("vesterian")
NO_MENTIONS = discord.AllowedMentions.none()


class ResultView(discord.ui.View):
    def __init__(self, app, interaction, result, command, query="", view=None):
        super().__init__(timeout=600)
        self.app, self.owner = app, interaction.user.id
        self.guild, self.channel = interaction.guild_id, interaction.channel_id
        self.created = time.monotonic()
        self.message = None
        self.command, self.query, self.section = command, query, view
        self.result, self.pages, self.page = result, result.embeds(), 0
        self.busy = False
        self.history = []
        self.rebuild()

    def attachments(self):
        return (
            [discord.File(io.BytesIO(self.result.chart), filename="price.png")] if self.result.chart else []
        )

    def rebuild(self):
        self.clear_items()
        if len(self.pages) > 1:
            for title, delta in [("Previous", -1), ("Next", 1)]:
                button = discord.ui.Button(label=title, disabled=not 0 <= self.page + delta < len(self.pages))

                async def turn(interaction, step=delta):
                    self.page = max(0, min(len(self.pages) - 1, self.page + step))
                    self.rebuild()
                    await interaction.response.edit_message(
                        embed=self.pages[self.page], view=self, allowed_mentions=NO_MENTIONS
                    )

                button.callback = turn
                self.add_item(button)
        if self.result.choices:
            select = discord.ui.Select(
                placeholder="Choose the exact entry",
                options=[
                    discord.SelectOption(
                        label=e.name[:100], value=e.key, description=(e.kind + " " + e.context)[:100]
                    )
                    for e in self.result.choices[:25]
                ],
            )

            async def pick(interaction):
                entry = next((e for e in self.result.choices if e.key == select.values[0]), None)
                if entry is None:
                    await interaction.response.send_message(
                        "Selection expired. Run the command again.", ephemeral=True
                    )
                    return
                command = entry.kind if self.command == "search" else self.command
                query = entry.path.rsplit("/", 1)[-1] if command == "updates" else entry.key
                await self.replace(
                    interaction, command, query, self.section if self.command != "search" else None
                )

            select.callback = pick
            self.add_item(select)
        elif self.command in VIEWS:
            select = discord.ui.Select(
                placeholder="Choose a view",
                options=[
                    discord.SelectOption(label=name.capitalize(), value=name, default=name == self.section)
                    for name in VIEWS[self.command]
                ],
            )

            async def change(interaction):
                await self.replace(interaction, self.command, self.query, select.values[0])

            select.callback = change
            self.add_item(select)
        if self.section == "chart" and self.command == "price" and not self.result.choices:
            ranges = discord.ui.Select(
                placeholder="Time range",
                options=[
                    discord.SelectOption(label=k, value=k, default=k == self.result.chart_range)
                    for k in RANGES
                ],
            )

            async def change_range(interaction):
                await self.replace(
                    interaction,
                    self.command,
                    self.query,
                    "chart",
                    window=ranges.values[0],
                    metric=self.result.chart_metric,
                )

            ranges.callback = change_range
            self.add_item(ranges)
            metrics = discord.ui.Select(
                placeholder="Price metric",
                options=[
                    discord.SelectOption(label=v, value=k, default=k == self.result.chart_metric)
                    for k, v in METRICS.items()
                ],
            )

            async def change_metric(interaction):
                await self.replace(
                    interaction,
                    self.command,
                    self.query,
                    "chart",
                    window=self.result.chart_range,
                    metric=metrics.values[0],
                )

            metrics.callback = change_metric
            self.add_item(metrics)
        if self.result.related:
            related = discord.ui.Select(
                placeholder="Explore related entries (up to 25)",
                options=[
                    discord.SelectOption(
                        label=e.name[:100], value=e.key, description=(e.kind + " " + e.context)[:100]
                    )
                    for e in self.result.related
                ],
            )

            async def explore(interaction):
                entry = next((e for e in self.result.related if e.key == related.values[0]), None)
                if entry is None:
                    raise UserError("Selection expired. Run the command again.")
                await self.replace(interaction, entry.kind, entry.key, None, navigate=True)

            related.callback = explore
            self.add_item(related)
        for title, command, query, section in self.result.actions:
            button = discord.ui.Button(label=title)

            async def navigate(interaction, c=command, q=query, v=section):
                await self.replace(interaction, c, q, v, navigate=True)

            button.callback = navigate
            self.add_item(button)
        if self.history:
            back = discord.ui.Button(label="Back")

            async def go_back(interaction):
                c, q, v, options = self.history[-1]
                await self.replace(interaction, c, q, v, backwards=True, **options)

            back.callback = go_back
            self.add_item(back)
        if self.result.url:
            self.add_item(discord.ui.Button(label="Open database page", url=self.result.url))

    async def interaction_check(self, interaction):
        valid = (
            interaction.user.id == self.owner
            and interaction.guild_id == self.guild
            and interaction.channel_id == self.channel
            and time.monotonic() - self.created < 600
            and (self.message is None or interaction.message.id == self.message.id)
        )
        try:
            if not valid:
                raise UserError(
                    "These controls belong to the original requester or have expired. Run your own command."
                )
            if self.busy:
                raise UserError("Your previous selection is still loading.")
            self.app.admit(interaction, self.command, component=True)
            return True
        except UserError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS)
            return False

    async def replace(self, interaction, command, query, section, navigate=False, backwards=False, **options):
        self.busy = True
        self.app.active[self.owner] += 1
        try:
            await interaction.response.defer()
            async with asyncio.timeout(15):
                result = await self.app.service.run(command, query, section, **options)
            if backwards:
                self.history.pop()
            elif navigate:
                self.history.append(
                    (
                        self.command,
                        self.query,
                        self.section,
                        {"window": self.result.chart_range, "metric": self.result.chart_metric},
                    )
                )
                self.history = self.history[-20:]
            self.command, self.query = command, query
            self.section = section or VIEWS.get(command, (None,))[0]
            self.result, self.pages, self.page = result, result.embeds(), 0
            self.rebuild()
            await interaction.edit_original_response(
                embed=self.pages[0], view=self, attachments=self.attachments(), allowed_mentions=NO_MENTIONS
            )
        except (UserError, TimeoutError) as exc:
            await interaction.followup.send(
                str(exc) if isinstance(exc, UserError) else "Request timed out. Try again.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        finally:
            self.busy = False
            self.app.active[self.owner] -= 1
            if not self.app.active[self.owner]:
                del self.app.active[self.owner]

    async def on_error(self, interaction, error, item):
        self.app.metrics["errors"] += 1
        log.error("component_failed type=%s", type(error).__name__)
        send = (
            interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        )
        await send(
            "This result could not be displayed. Please run the command again.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self):
        for child in self.children:
            if not getattr(child, "url", None):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass


class Bot(discord.Client):
    def __init__(self, directory=Path(".runtime"), sync=False, guild_id=None):
        super().__init__(
            intents=discord.Intents.none(),
            allowed_mentions=NO_MENTIONS,
            max_messages=None,
            activity=discord.Game(name="Vesteria • /help"),
            status=discord.Status.online,
        )
        self.tree = app_commands.CommandTree(self)
        self.directory, self.do_sync, self.test_guild = directory, sync, guild_id
        self.limits, self.active, self.seen = Limits(), Counter(), OrderedDict()
        self.metrics = Counter()
        self.service = None
        self.session = self.worker = None
        register(self)

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=2),
            cookie_jar=aiohttp.DummyCookieJar(),
            headers={"Accept": "application/json", "User-Agent": "Vesterian/0.1 (Discord game lookup)"},
        )
        repo = Repository(HttpSource(self.session), self.directory)
        await asyncio.to_thread(repo.load)
        self.service = Service(repo)
        try:
            existing = {e.name: e for e in await self.fetch_application_emojis()}
            for name, _, _ in DENOMINATIONS:
                key = "vesteria_" + name.lower()
                emoji = existing.get(key)
                if emoji is None:
                    emoji = await self.create_application_emoji(
                        name=key,
                        image=(Path(__file__).parent / "assets" / f"{name.lower()}.png").read_bytes(),
                    )
                ICONS[name] = str(emoji)
        except (discord.HTTPException, OSError):
            log.warning("currency_icons_unavailable_using_fallback")
        self.worker = asyncio.create_task(self.refresh_loop())
        if self.do_sync:
            if self.test_guild:
                guild = discord.Object(id=self.test_guild)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()

    async def refresh_loop(self):
        while True:
            try:
                await self.service.repo.refresh()
                log.info("catalog_refreshed")
            except SourceError:
                log.warning("catalog_refresh_failed")
            except Exception as exc:
                self.service.repo.refresh_error = True
                self.service.repo.metrics["refresh_failures"] += 1
                log.error("catalog_refresh_unexpected type=%s", type(exc).__name__)
            await asyncio.sleep(random.uniform(810, 990))

    def admit(self, interaction, command, component=False):
        if interaction.guild_id is None:
            raise UserError("This version works in servers. Add the app to your server first.")
        now = time.monotonic()
        while self.seen and (next(iter(self.seen.values())) < now - 900 or len(self.seen) >= 10000):
            self.seen.popitem(last=False)
        if interaction.id in self.seen:
            raise UserError("This interaction was already handled.")
        if self.active[interaction.user.id] >= 2 or sum(self.active.values()) >= 50:
            raise UserError("Too many requests are already running. Please wait.")
        try:
            self.limits.take(interaction.user.id, interaction.guild_id, 2 if command == "price" else 1)
        except UserError:
            self.metrics["rate_limited"] += 1
            log.info("request_rate_limited command=%s component=%s", command, component)
            raise
        self.metrics["accepted"] += 1
        self.seen[interaction.id] = now

    async def respond(self, interaction, command, query="", view=None, public=False, **options):
        try:
            self.admit(interaction, command)
        except UserError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        uid = interaction.user.id
        self.active[uid] += 1
        try:
            await interaction.response.defer(
                ephemeral=not public or command in {"search", "help", "status"}, thinking=True
            )
            async with asyncio.timeout(15):
                result = await self.service.run(command, query, view, **options)
            if command == "status":
                result.lines.append(
                    f"Since startup: {self.metrics['accepted']} admitted interactions; "
                    f"{self.metrics['rate_limited']} rate-limit rejections; {self.metrics['errors']} display errors."
                )
            controls = ResultView(
                self, interaction, result, command, query, view or VIEWS.get(command, (None,))[0]
            )
            controls.message = await interaction.edit_original_response(
                embed=controls.pages[0],
                view=controls,
                attachments=controls.attachments(),
                allowed_mentions=NO_MENTIONS,
            )
        except (UserError, TimeoutError) as exc:
            await interaction.edit_original_response(
                content=str(exc) if isinstance(exc, UserError) else "Request timed out. Please try again.",
                embed=None,
                view=None,
                allowed_mentions=NO_MENTIONS,
            )
        except Exception as exc:
            self.metrics["errors"] += 1
            log.error("command_failed command=%s type=%s", command, type(exc).__name__)
            await interaction.edit_original_response(
                content="This result could not be displayed. Try again later.",
                embed=None,
                view=None,
                allowed_mentions=NO_MENTIONS,
            )
        finally:
            self.active[uid] -= 1
            if not self.active[uid]:
                del self.active[uid]

    async def autocomplete(self, interaction, current):
        try:
            if not self.service or not interaction.guild_id:
                return []
            self.limits.take(interaction.user.id, interaction.guild_id, autocomplete=True)
            command = interaction.command.name
            kind = None if command == "search" else "item" if command == "price" else command
            return [
                app_commands.Choice(
                    name=(e.name + (" — " + e.context if e.context else ""))[:100], value=e.key
                )
                for e in self.service.repo.ready_index().search(current, kind)
            ]
        except UserError:
            return []

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.service:
            await self.service.repo.close()
        if self.session:
            await self.session.close()
        await super().close()


def register(bot):
    tree = bot.tree

    @tree.error
    async def command_error(interaction, error):
        log.warning("interaction_rejected type=%s", type(error).__name__)
        send = (
            interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        )
        await send(
            "That command could not be processed. Check its options and try again.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    @tree.command(description="Find an item, ability, monster, map or update.")
    @app_commands.guild_only()
    async def search(
        interaction: discord.Interaction,
        query: app_commands.Range[str, 1, 100],
        type: Literal["item", "ability", "monster", "map", "updates"] | None = None,
    ):
        await bot.respond(interaction, "search", query, type=type)

    @tree.command(description="Item stats, acquisition sources, crafting and reforging.")
    @app_commands.guild_only()
    async def item(
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        view: Literal["overview", "sources", "crafting", "details"] = "overview",
        public: bool = False,
    ):
        await bot.respond(interaction, "item", name, view, public)

    @tree.command(description="Ability behavior, variants and rank progression.")
    @app_commands.guild_only()
    async def ability(
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        view: Literal["overview", "ranks", "details"] = "overview",
        public: bool = False,
    ):
        await bot.respond(interaction, "ability", name, view, public)

    @tree.command(description="Monster combat stats, drops and known spawn locations.")
    @app_commands.guild_only()
    async def monster(
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        view: Literal["overview", "drops", "locations", "details"] = "overview",
        public: bool = False,
    ):
        await bot.respond(interaction, "monster", name, view, public)

    @tree.command(description="Map overview, monsters, NPCs, shops and exits.")
    @app_commands.guild_only()
    async def map(
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        view: Literal["overview", "monsters", "npcs", "exits"] = "overview",
        public: bool = False,
    ):
        await bot.respond(interaction, "map", name, view, public)

    @tree.command(description="Recent sale prices, asking prices and daily history.")
    @app_commands.guild_only()
    async def price(
        interaction: discord.Interaction,
        item: app_commands.Range[str, 1, 100],
        view: Literal["summary", "chart", "history"] = "summary",
        public: bool = False,
    ):
        await bot.respond(interaction, "price", item, view, public)

    @tree.command(description="Premium Shop codes with rewards, requirements and expiry.")
    @app_commands.guild_only()
    async def codes(
        interaction: discord.Interaction, show: Literal["active", "all"] = "active", public: bool = False
    ):
        await bot.respond(interaction, "codes", public=public, show=show)

    @tree.command(description="Latest game update, or a release containing a version number.")
    @app_commands.guild_only()
    async def updates(
        interaction: discord.Interaction,
        version: app_commands.Range[int, 1, 999999] | None = None,
        public: bool = False,
    ):
        await bot.respond(interaction, "updates", str(version) if version else "", public=public)

    @tree.command(description="Command options and interaction guidance.")
    @app_commands.guild_only()
    async def help(interaction: discord.Interaction, command: str = ""):
        await bot.respond(interaction, "help", command)

    @tree.command(description="Catalog readiness, freshness and source health.")
    @app_commands.guild_only()
    async def status(interaction: discord.Interaction):
        await bot.respond(interaction, "status")

    for cmd, arg in [
        (search, "query"),
        (item, "name"),
        (ability, "name"),
        (monster, "name"),
        (map, "name"),
        (price, "item"),
    ]:
        cmd.autocomplete(arg)(bot.autocomplete)


def main():
    parser = argparse.ArgumentParser(description="Run the Vesterian Discord app.")
    parser.add_argument(
        "--sync", action="store_true", help="Register commands once when intentionally requested"
    )
    parser.add_argument(
        "--guild", type=int, help="Register to a test server instead of globally (with --sync)"
    )
    parser.add_argument("--data-dir", type=Path, default=Path(".runtime"))
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False, interpolate=False)
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        parser.error("Set DISCORD_TOKEN in your local environment or deployment secret store.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # SDK debug logs may include request details; never enable them in production.
    logging.getLogger("discord").setLevel(logging.WARNING)
    Bot(args.data_dir, args.sync, args.guild).run(token, log_handler=None)


if __name__ == "__main__":
    main()
