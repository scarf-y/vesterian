"""Map Vesteria's public routes from official indexes and sample JSON responses.

Python 3.10+, standard library only. No login, browser automation, or asset downloads.
Run: python scripts/map_vesteria.py
"""

import argparse
import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://db.playvesteria.com"
AGENT = "VesterianRouteMapper/0.1 (public API discovery)"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
      "image": "http://www.google.com/schemas/sitemap-image/1.1"}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical(url):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, url))
    if parsed.scheme != "https" or parsed.netloc != "db.playvesteria.com":
        return None
    return urllib.parse.urlunsplit(parsed._replace(path=parsed.path or "/", fragment=""))


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not canonical(newurl):
            raise ValueError("Refusing redirect outside the database origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href and (url := canonical(href)):
                self.links.add(url)


class Client:
    def __init__(self, output, delay):
        self.output = output
        self.delay = delay
        self.last_request = 0
        self.log = []
        self.robot = None
        self.opener = urllib.request.build_opener(SameOriginRedirect())
        self.cache = output / "responses"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cache_index_path = output / "cache-index.json"
        self.cache_index = json.loads(self.cache_index_path.read_text(encoding="utf-8")) if self.cache_index_path.exists() else {}

    def get(self, path, accept="*/*", conditional=False):
        url = canonical(path)
        if not url:
            raise ValueError(f"Not a same-origin URL: {path}")
        if self.robot and not self.robot.can_fetch(AGENT, url):
            self.log.append({"url": url, "skipped": "robots.txt"})
            return None, self.log[-1]
        key = url + "|" + accept
        file = self.cache / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".body")
        headers = {"User-Agent": AGENT, "Accept": accept}
        prior = self.cache_index.get(key, {})
        if conditional and file.exists() and prior.get("etag"):
            headers["If-None-Match"] = prior["etag"]
        for attempt in range(3):
            time.sleep(max(0, self.last_request + self.delay - time.monotonic()))
            self.last_request = time.monotonic()
            started = time.monotonic()
            try:
                response = self.opener.open(urllib.request.Request(url, headers=headers), timeout=45)
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, TimeoutError) as error:
                record = {"url": url, "error": str(error), "checkedAt": datetime.now(timezone.utc).isoformat()}
                self.log.append(record)
                print(json.dumps(record), flush=True)
                return None, record
            with response:
                body = response.read(64 * 1024 * 1024 + 1)
                if len(body) > 64 * 1024 * 1024:
                    raise ValueError(f"Response exceeded 64 MiB: {url}")
                record = {
                    "url": url, "finalUrl": response.geturl(), "status": response.status,
                    "contentType": response.headers.get("Content-Type"),
                    "etag": response.headers.get("ETag"),
                    "cacheControl": response.headers.get("Cache-Control"),
                    "license": response.headers.get("Link"),
                    "bytesDownloaded": len(body), "seconds": round(time.monotonic() - started, 3),
                    "checkedAt": datetime.now(timezone.utc).isoformat(),
                    "ifNoneMatchSent": "If-None-Match" in headers,
                }
                self.log.append(record)
                print(f"{response.status} {url} ({len(body):,} bytes)", flush=True)
                if response.status in (429, 503) and attempt < 2:
                    retry = response.headers.get("Retry-After", "")
                    if retry and (not retry.isdigit() or int(retry) > 30):
                        return None, record
                    time.sleep(int(retry) if retry.isdigit() else 2 ** (attempt + 1))
                    continue
                if response.status == 304 and file.exists():
                    body = file.read_bytes()
                    record["contentType"] = prior.get("contentType")
                    record["etag"] = record["etag"] or prior.get("etag")
                    record["reusedCachedBody"] = True
                elif response.status == 200:
                    file.write_bytes(body)
                else:
                    return None, record
                record["bodyFile"] = file.relative_to(self.output).as_posix()
                self.cache_index[key] = {"etag": record["etag"], "contentType": record["contentType"], "bodyFile": record["bodyFile"]}
                write_json(self.cache_index_path, self.cache_index)
                return body, record
        return None, record


def family(url):
    path = urllib.parse.urlsplit(url).path
    return path.strip("/").split("/")[0] or "/"


def shape(data):
    if isinstance(data, dict):
        return {"type": "object", "keys": list(data), "count": data.get("count"),
                "arrays": {k: len(v) for k, v in data.items() if isinstance(v, list)}}
    return {"type": type(data).__name__, "length": len(data) if isinstance(data, list) else None}


def run(output, delay):
    output.mkdir(parents=True, exist_ok=True)
    client = Client(output, delay)
    sources = output / "sources"
    sources.mkdir(exist_ok=True)
    urls = {}
    images = set()
    sitemap_counts = {}
    probes = []

    def add(url, source):
        if url := canonical(url):
            urls.setdefault(url, set()).add(source)

    def source(path, filename=None):
        body, record = client.get(path, conditional=True)
        if body is None:
            raise RuntimeError(f"Required discovery source unavailable: {path}")
        (sources / (filename or path.lstrip("/").replace("/", "-"))).write_bytes(body)
        return body

    try:
        robots = source("/robots.txt").decode("utf-8")
        client.robot = urllib.robotparser.RobotFileParser()
        client.robot.parse(robots.splitlines())
        spec = json.loads(source("/openapi.json"))
        source("/llms.txt")
        catalog = json.loads(source("/api/catalog", "api-catalog.json"))
        queue = client.robot.site_maps() or [BASE + "/sitemap.xml"]
        seen = set()
        while queue:
            url = canonical(queue.pop(0))
            if not url or url in seen:
                continue
            if len(seen) >= 40:
                raise RuntimeError("Sitemap discovery exceeded its 40-document limit")
            seen.add(url)
            body = source(url, urllib.parse.urlsplit(url).path.strip("/").replace("/", "-"))
            root = ET.fromstring(body)
            if root.tag.endswith("sitemapindex"):
                queue.extend(el.text for el in root.findall("s:sitemap/s:loc", NS) if el.text)
            else:
                entries = root.findall("s:url", NS)
                sitemap_counts[url] = len(entries)
                for entry in entries:
                    loc = entry.find("s:loc", NS)
                    if loc is not None and loc.text:
                        add(loc.text, url)
                    images.update(el.text for el in entry.findall("image:image/image:loc", NS) if el.text)

        sitemap_urls = set(urls)
        # Only navigation pages, not a recursive HTML crawl of every asset page.
        for path in ["/", "/developers", "/content-license", "/guides", "/guilds", "/dashboards"]:
            body, record = client.get(path, accept="text/html")
            if body:
                parser = Links()
                parser.feed(body.decode("utf-8"))
                for url in parser.links:
                    add(url, "navigation:" + path)

        endpoints = []
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                endpoints.append({"path": path, "method": method.upper(), "summary": operation.get("summary"),
                                  "parameters": operation.get("parameters", [])})

        sample_paths = [
            "/api/items", "/api/items?search=shiny%20golden%20gift",
            "/items?format=json&search=tier%3A7%20tag%3Astaff",
            "/items/stick?format=json", "/items/holy-scroll?variation=magnificent&format=json",
            "/api/abilities", "/api/abilities?search=rock",
            "/abilities?format=json", "/abilities/adventurer?format=json",
            "/abilities/adventurer/rock-throw?format=json",
            "/abilities/adventurer/rock-throw?variation=boulder-throw&format=json",
            "/api/monsters", "/api/monsters?form=variation&page=99",
            "/api/monsters/acid-slime", "/api/monsters/bandit?variation=hobo",
            "/monsters?format=json", "/monsters/acid-slime?format=json",
            "/api/maps", "/api/maps/nilgarf", "/maps?format=json", "/maps/nilgarf?format=json",
            "/search?q=Magnificent%20Holy%20Scroll&format=json&limit=100",
            "/codes?format=json", "/market?format=json",
            "/market/holy-scroll?format=json", "/items/holy-scroll/market-history",
            "/dashboards?format=json", "/dashboards/badlands-pvp?format=json",
            "/versions?format=json", "/videos?format=json",
            "/guides?format=json", "/guilds?format=json",
            "/popular?format=json", "/recently-updated?format=json",
        ]
        versions = catalog.get("versions", [])
        if versions:
            latest = max(versions, key=lambda v: v.get("lastVersion", v.get("version", 0)))
            sample_paths.append(latest["url"] + "?format=json")
        videos = sorted(url for url in sitemap_urls if urllib.parse.urlsplit(url).path.startswith("/videos/"))
        if videos:
            sample_paths.append(videos[0] + "?format=json")
        for path in sample_paths:
            body, record = client.get(path)
            probe = {"url": record["url"], "status": record.get("status"), "contentType": record.get("contentType"), "bodyFile": record.get("bodyFile")}
            if body:
                try:
                    data = json.loads(body)
                    probe["json"] = True
                    probe["shape"] = shape(data)
                except (ValueError, UnicodeDecodeError):
                    probe["json"] = False
            probes.append(probe)

        # Check the documented conditional request behavior, reusing our saved body.
        _, etag_check = client.get("/api/catalog", conditional=True)
        # Check content negotiation separately from explicit ?format=json.
        body, record = client.get("/items/stick", accept="application/json")
        negotiation = {"status": record.get("status"), "contentType": record.get("contentType")}
        try:
            negotiation["isJson"] = isinstance(json.loads(body), dict) if body else False
        except ValueError:
            negotiation["isJson"] = False

        records = [{"url": url, "path": urllib.parse.urlsplit(url).path,
                    "query": urllib.parse.urlsplit(url).query, "family": family(url),
                    "inSitemap": url in sitemap_urls,
                    "robotsAllowed": client.robot.can_fetch(AGENT, url),
                    "sources": sorted(origins)} for url, origins in sorted(urls.items())]
        write_json(output / "urls.json", records)
        with (output / "urls.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["url", "path", "query", "family", "inSitemap", "robotsAllowed", "sources"])
            writer.writeheader()
            writer.writerows({**r, "sources": " | ".join(r["sources"])} for r in records)
        write_json(output / "image-urls.json", sorted(images))
        write_json(output / "openapi-routes.json", endpoints)
        write_json(output / "endpoint-checks.json", probes)
        summary = {
            "checkedAt": datetime.now(timezone.utc).isoformat(), "baseUrl": BASE,
            "scope": "Public sitemap enumeration, six navigation pages, and representative read-only JSON requests; not every page was fetched.",
            "catalogBuild": catalog.get("build"), "catalogCounts": catalog.get("counts"),
            "catalogArrayLengths": {k: len(v) for k, v in catalog["assets"].items()},
            "sitemaps": sitemap_counts, "uniqueSitemapUrls": len(sitemap_urls),
            "sitemapFamilies": dict(sorted(Counter(family(url) for url in sitemap_urls).items())),
            "uniqueDiscoveredUrls": len(records), "imageUrls": len(images),
            "openapiOperations": len(endpoints), "sampledEndpoints": len(probes),
            "successfulJsonSamples": sum(p.get("json", False) for p in probes),
            "catalogConditionalGetStatus": etag_check.get("status"),
            "acceptHeaderCheck": negotiation, "requests": len(client.log),
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        write_json(output / "requests.json", client.log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "vesteria")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between sequential requests (at least 0.5)")
    args = parser.parse_args()
    if args.delay < 0.5:
        parser.error("--delay must be at least 0.5 seconds")
    run(args.output, args.delay)
