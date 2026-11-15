# Vesterian

Discovery and design work for a Discord information bot backed by the official [Vesteria Database](https://db.playvesteria.com).

Start with the [bot design](docs/bot-design.md) for commands, source updates, request limits, and security, or the [site and API route map](docs/vesteria-route-map.md) for endpoint details. This repository currently contains discovery code and design artifacts; the Discord bot has not been implemented yet.

## Artifacts

| File | Purpose |
| --- | --- |
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
