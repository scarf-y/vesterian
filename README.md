# Vesterian

A Discord information bot backed by the official [Vesteria Database](https://db.playvesteria.com), with ten slash commands, exact-variant lookup, private replies, pagination, caching, and request limits.

Read the [command walkthrough](docs/commands.md) for every option, reply and interaction. The [bot design](docs/bot-design.md) describes the broader roadmap; the [route map](docs/vesteria-route-map.md) documents the source API. This is a single-process functional release. Runtime limits are in the Python modules; the example policy JSON is not loaded.

## Run the bot

Requires Python 3.11 or newer. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -e . --no-deps
```

Create a Discord application/bot in the Developer Portal. Install it to a test server using the `bot` and `applications.commands` scopes. Grant View Channels, Send Messages, Embed Links, and Attach Files (for charts); do not grant Administrator or enable privileged intents. This implementation receives interactions through the Gateway, so leave the Interactions Endpoint URL unset.

Provide `DISCORD_TOKEN` through your local environment or deployment secret store. The bot automatically loads `.env` from the project root; existing environment variables take precedence. `.env.example` documents the variable. Never paste the token into a Git-tracked file or chat.

Register commands to a test server and start the bot (replace the example server ID):

```powershell
.\.venv\Scripts\python -m vesterian.bot --sync --guild 123456789012345678
```

For subsequent starts, omit `--sync`. To register globally, run once with `--sync` and no `--guild`; remove test-guild command registrations in Discord when moving to global registration to avoid overlapping command sets. Sync is an intentional operation, not something performed on every reconnect.

```powershell
.\.venv\Scripts\python -m vesterian.bot
```

The first catalog refresh runs in the background; `/help` and `/status` work while it loads. `.runtime/catalog.json` stores the last accepted catalog. A cold start with no usable catalog explains that data commands are not ready. No credentials or interaction tokens are saved there. Run only one instance; shared quotas and multi-instance coordination are future work.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check vesterian tests
```

Tests use reduced captured responses and mocked Discord/HTTP transports; they need no credentials or network access. They cover command views, exact variants, ambiguity, code expiry, market freshness, catalog replacement, shared fetches, rate limits, safe URLs, payload bounds, private/public replies and component ownership. GitHub Actions runs the same checks on Python 3.11 and 3.13. A real Discord server check remains necessary before public rollout.

## Artifacts

| File | Purpose |
| --- | --- |
| [Command walkthrough](docs/commands.md) | Implemented options, user interactions, failure messages and limitations |
| [Bot design](docs/bot-design.md) | Proposed commands, architecture, update handling, rate limits, security, and implementation sequence |
| [Policy example](config/bot-policy.example.json) | Proposed numeric defaults; not loaded or enforced by an application yet |
| [Route map](docs/vesteria-route-map.md) | URL hierarchy, JSON endpoints, response shapes, and bot integration notes |
| [URL inventory (CSV)](data/vesteria/urls.csv) | Searchable list of sitemap and navigation URLs, with provenance |
| [URL inventory (JSON)](data/vesteria/urls.json) | The same inventory for programmatic use |
| [Summary](data/vesteria/summary.json) | Crawl scope, counts, catalog build, and verification totals |
| [Endpoint checks](data/vesteria/endpoint-checks.json) | Sample request status, content type, JSON keys, and local response file |
| [OpenAPI routes](data/vesteria/openapi-routes.json) | Operations explicitly included in the site's OpenAPI specification |
| [Request log](data/vesteria/requests.json) | Timestamps, status, redirects, cache headers, and sizes |
| [Image URL inventory](data/vesteria/image-urls.json) | Image links advertised in the image sitemap; image files were not downloaded |

## Refresh the inventory

Requires Python 3.10 or newer. No additional packages are needed.

```powershell
python scripts/map_vesteria.py
```

The script reads `robots.txt`, the official API guide, OpenAPI, the catalog, and the published sitemaps. It then samples six public navigation pages and representative JSON routes. It sends sequential GET requests, at least one second apart by default, honors robots exclusions, and revalidates cached discovery sources when an ETag is available. It does not visit every discovered URL.

```powershell
python scripts/map_vesteria.py --delay 2 --output data/vesteria
```

`responses/` contains local response bodies. `cache-index.json` maps requests to those files. The larger local cache and catalog snapshot are excluded from Git; running the script recreates them. Endpoint availability and counts can change. The human-written route map is a dated snapshot; refreshing the script updates the machine-readable reports.

Game content is supplied by Vesteria, Inc. under the [Vesteria Content License](https://db.playvesteria.com/content-license). This is an independent bot project.

## Quick start on Windows

With `DISCORD_TOKEN` in your project `.env`, double-click `start-bot.cmd`, or run `.\start-bot.cmd` in PowerShell. For first-time command registration, run `.\start-bot.cmd --sync --guild YOUR_SERVER_ID`. Keep the terminal open; Ctrl+C stops the bot.
