# Commands and interaction walkthrough

Implemented in v0.1.0. Commands work in servers. The bot never reads normal channel messages and does not require privileged intents.

## Visual presentation and charts

Overview cards now prioritize short descriptions and inline fields: item requirements and main stats, monster combat stats, ability cost/cooldown/range, and map inhabitants. Item, monster and ability **Details** views retain the fuller information. Drop rows use consistent chance/quantity/attempt labels, with conditions below. Empty location and market results suggest what to try next.

Market summaries separate average sales, median sales, asking prices and activity into labeled fields. Charts include latest recorded price, percentage change between the displayed observation dates, and selected-range transaction/item totals above the image. A volume panel shows items sold alongside transactions, and the price line labels its last observed value. A single observation or a zero starting price is labeled **Not comparable** instead of inventing a percentage. Counts cover available non-legacy buckets, not guaranteed complete coverage.

Freshness stays in the footer; failed refreshes also show a visible saved-data notice. Tier colors apply to items, while other cards share the same accent. Long fields paginate within Discord's size limits. Restart to use the new dropdown views; sync commands again if you want **Details** directly in slash-command options.

Items now show their exact variant thumbnail, a tier badge, and the tier's color from the database. Missing tiers are not guessed. Abilities use their icons; monsters use portraits when the source marks an image available; maps show the minimap and key art.

Prices and item currency values use Platinum, Gold, Silver and Bronze, retaining fractional Bronze in averages. On startup the bot reuses or creates four application-owned currency emojis from the bundled official icons. This requires no server emoji management permission. If Discord rejects emoji setup, readable colored symbols and denomination names remain available. Graphs always include the bundled currency image.

Run `/price item:Holy Scroll`, then choose **Chart** in the view dropdown. You can also use `view:chart` after syncing the updated command definitions. Chart controls select **24h / 7d / 30d** and **Average sale / Median sale / Average asking price**. Each change replaces the image in the same message. Only the requester can use the controls, under the existing rate limits and ten-minute expiry.

24h charts use hourly history; longer ranges use daily history. Timestamps are UTC. Legacy per-sale days are excluded to avoid mixing them with per-item prices. Missing values remain gaps, and an empty range explicitly reports no comparable data. Changing controls reuses cached history; it does not fetch for every click. The graph is a PNG redrawn by Discord controls, not a hoverable browser plot. Text history remains available.

Update dependencies with `python -m pip install -r requirements.txt` in your virtual environment, then restart the bot. Allow **Attach Files** and **Embed Links** for chart replies in the target channel. The runtime policy example remains a future configuration specification, not a loaded settings file.

## How a lookup feels

1. Type `/item`, then start typing `Holy Scroll` in **name**. Suggestions show exact variants as separate entries. Suggestions come from the local index.
2. Select **Magnificent Holy Scroll**. Run the command; Discord shows a private loading response while the bot retrieves the exact variation.
3. The result shows the exact name, relevant stats, a check timestamp, and an **Open database page** link with the variation preserved.
4. Use **Choose a view** to switch to Sources or Crafting. The same message updates. Long results have **Previous** and **Next** buttons.
5. To share a result with the channel, run the data command with `public:true`. The default is private. Other people can open the source link, but only the original requester can operate the result's controls.

If a typed name matches multiple entries, the bot shows **Choose the exact entry**. It does not choose an arbitrary match. The selector offers up to 25 entries; narrow the search if needed. Controls expire after ten minutes and are disabled when the bot can edit the message. After a process restart, run the command again.

## Implemented commands

| Command and example | Options | Exact response |
| --- | --- | --- |
| `/search query:scroll` | Required `query`; optional `type` = item, ability, monster, map, updates | Private list of up to 25 matching records with their types/context and a selector. Choosing an entry opens its default view. Search is local, with no remote fallback in v0.1. |
| `/item name:Stick` | `view` = overview, sources, crafting; `public` | Overview: description, tier, equipment type, classes, binding, behavior, structured stats and granted abilities. Sources: acquisition table, exact source links, methods, chance/amount. Crafting: ingredients and outputs for crafting, reforging, and recipes using the item. |
| `/ability name:Rock Throw view:ranks` | `view` = overview, ranks; `public` | Overview: mechanics, requirements, stats, behavior notes, differences and interactions. Ranks: each supplied rank table with column labels and separate upgrade paths. Select exact variants through autocomplete or `/search`. |
| `/monster name:Acid Slime view:drops` | `view` = overview, drops, locations; `public` | Overview: book stats, combat figures, behavior and bounty milestones. Drops: exact item link, chance per attempt, quantity per success, attempt count and conditions. Locations: recorded maps and spawn-region counts. |
| `/map name:Nilgarf view:npcs` | `view` = overview, monsters, npcs, exits; `public` | Overview: tags, counts, calibration and export timestamp. Other views list linked monsters, NPC roles/shop names, or exits. An exit without a URL stays labeled as having no map page. |
| `/price item:Holy Scroll` | `view` = summary, chart, history; `public` | Summary: 24-hour and supplied longer-window average/median sales, asking averages, purchases, units and source timestamp. History: newest daily entries first, labeled per-item or legacy per-sale. Values use Platinum, Gold, Silver and Bronze with fractional Bronze preserved. Chart provides range and metric controls. |
| `/codes show:active` | `show` = active, all; `public` | Code names, rewards, level/premium/other requirements and expiry. Known expired/upcoming codes are excluded from active. Stale data says “Last seen; unverified.” The bot does not redeem codes. |
| `/updates version:1359` | Optional `version` number; `public` | Without a number, show the newest catalog release. A number can select a grouped release containing it. Display summary, release-wide notes, footnotes and individual changes, without double-counting the grouped copies. |
| `/help command:item` | Optional `command` name | Private command guidance. Omit the option to see all commands. Works without source data. |
| `/status` | None | Private catalog readiness, last validation time, build time and whether refresh failed. Detail records have separate check times. No tokens, host details or internal errors. |

All names support manual text or autocomplete. A suggestion's stored value identifies an exact catalog URL, including `variation`; it is validated again at execution. Search/help/status always remain private. `public` is available only on the seven data commands.

## Example result content

`/item name:Stick view:sources` returns the database's acquisition information, such as Treemuk and the exact Shroommuk variation, with the recorded drop chance. `/item name:Stick view:crafting` shows its reforge recipe, including ten Oak Wood. These examples come from the captured test snapshot, not a promise about the current game.

`/monster name:Acid Slime view:drops` separates chance, amount and attempts. It does not add duplicate probabilities or imply a player's personal luck-adjusted chance.

`/price item:Holy Scroll` distinguishes actual sales from asking prices. History labels old stack-based data so it is not silently compared with newer per-item prices.

## Failure and access behavior

- Unknown name: ask the user to shorten the query or use `/search`; no arbitrary URL fetch.
- Ambiguous name: provide a selector; no automatic first-match answer.
- Exact entry removed/hidden or detail route returns 404: explain that entry is unavailable; never substitute the parent.
- No recorded drops, sources or locations: say information is not recorded; absence is not proof the game has none.
- Source temporarily fails: use a previously validated cache within the allowed age and label it. After the age limit, report unavailable.
- Fresh catalog format is incompatible or loses over 20% of a category: retain the saved catalog and expose refresh failure in `/status`.
- User/server/app quota exceeded: private wait message before fetching. Price uses two command tokens; other commands/buttons use one.
- Someone else clicks your controls: private explanation telling them to run their own command.
- A command fails after `public:true`: its public loading reply becomes a short generic error. Sensitive exception details are never included.

## Implemented limits and remaining design work

Runtime defaults are currently in the Python modules, covered by tests. `config/bot-policy.example.json` remains a design example and is **not loaded**. User buckets refill 20 tokens/minute with burst 5; server buckets 120/minute with burst 20; app bucket 600/minute with burst 40. There are at most two active operations per user and 50 overall. Autocomplete has separate quotas and makes no upstream requests.

Source requests are spaced at least one second apart with two concurrent slots and a 50-request bound. HTTP 429 pauses source traffic according to Retry-After; repeated failures pause it for a minute. There are no automatic immediate retries. Responses have time/size/depth limits. Concurrent cache misses share one fetch; 404s are remembered for 60 seconds. Catalog changes invalidate detail freshness but preserve older data as a labeled fallback where allowed.

Catalog polling runs every 15 minutes with jitter and conditional requests. The last accepted catalog survives restart through an atomic file replacement. Details, quota counters, interaction sessions and negative caches are in memory. Prices refresh on demand after 15 minutes, history after one hour, codes after 15 minutes, and ordinary details after six hours. The maximum unverified ages are one hour for prices/codes, six hours for history, and seven days for static data.

The original [design](bot-design.md) also describes future work: SQLite storage, seven-generation rollback, distributed quotas, per-feature circuit breakers, full operational metrics, global-search fallback, richer images, configurable policies and subscriptions. Those are not implemented by this first functional release. No automatic announcements or admin/configuration commands are enabled. A live Discord acceptance check is still required with the owner's credentials and test server.
