#!/usr/bin/env python3
"""Lightweight site crawler — fetches sitemap.xml + key pages so the agents know
what URLs already exist on the client site (so they can link to real pages
and avoid recommending duplicate slugs).

Stdlib only. Best-effort: failures degrade gracefully.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib import error, parse, request

from common import build_ssl_context, normalize_domain


USER_AGENT = "KCA-AI-Visibility-Crawler/1.0 (+https://kingcontractor.com)"


def _get(url: str, timeout: int = 15) -> str:
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with request.urlopen(req, timeout=timeout, context=build_ssl_context()) as r:
        raw = r.read()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")


def _candidate_sitemaps(domain: str) -> list[str]:
    base = f"https://{domain}"
    return [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
        f"{base}/sitemap1.xml",
        f"{base}/wp-sitemap.xml",
    ]


def _extract_loc_tags(xml_text: str) -> list[str]:
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, flags=re.IGNORECASE)


def _fetch_sitemap(domain: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for sm_url in _candidate_sitemaps(domain):
        try:
            text = _get(sm_url)
        except (error.HTTPError, error.URLError, TimeoutError):
            continue
        # Sitemap index → has child sitemaps; or url-set → has page URLs.
        locs = _extract_loc_tags(text)
        if not locs:
            continue
        if "<sitemap" in text.lower():
            # Sitemap index — recurse one level
            for child in locs[:8]:  # cap to avoid runaway
                try:
                    child_text = _get(child)
                except (error.HTTPError, error.URLError, TimeoutError):
                    continue
                for u in _extract_loc_tags(child_text):
                    if u not in seen and len(urls) < 500:
                        seen.add(u); urls.append(u)
        else:
            for u in locs:
                if u not in seen and len(urls) < 500:
                    seen.add(u); urls.append(u)
        if urls:
            return urls
    return urls


class _PageMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_description = ""
        self.h1: list[str] = []
        self.h2: list[str] = []
        self._in_title = False
        self._collect_into: list[str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True; self._buf = []
        elif tag in {"h1", "h2"}:
            self._collect_into = self.h1 if tag == "h1" else self.h2
            self._buf = []
        elif tag == "meta":
            d = dict(attrs)
            if (d.get("name") or "").lower() == "description":
                self.meta_description = (d.get("content") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self.title = " ".join(self._buf).strip(); self._in_title = False; self._buf = []
        elif tag in {"h1", "h2"} and self._collect_into is not None:
            text = " ".join(self._buf).strip()
            if text:
                self._collect_into.append(text[:200])
            self._collect_into = None; self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_title or self._collect_into is not None:
            self._buf.append(data)


def _page_meta(url: str) -> dict[str, Any]:
    try:
        html = _get(url)
    except (error.HTTPError, error.URLError, TimeoutError):
        return {"url": url, "ok": False, "title": "", "meta_description": "", "h1": [], "h2": []}
    p = _PageMetaParser()
    try:
        p.feed(html[:200_000])  # cap parse work
    except Exception:  # noqa: BLE001
        pass
    return {
        "url":               url,
        "ok":                True,
        "title":             p.title[:200],
        "meta_description":  p.meta_description[:300],
        "h1":                p.h1[:3],
        "h2":                p.h2[:8],
    }


def _categorise_url(url: str) -> str:
    path = parse.urlparse(url).path.lower()
    if path in ("", "/"):
        return "home"
    if any(seg in path for seg in ("/blog/", "/news/", "/articles/", "/post/", "/insights/", "/resources/")):
        return "blog"
    if any(seg in path for seg in ("/services/", "/service-areas/", "/roofing-services/")):
        return "service"
    if any(seg in path for seg in ("/contact", "/about", "/team", "/reviews")):
        return "info"
    if any(seg in path for seg in ("/locations/", "/areas/", "/cities/")):
        return "location"
    return "page"


def crawl_client(domain: str, *, max_pages_to_inspect: int = 10) -> dict[str, Any]:
    """Return a structured site map for the agents:
      - urls: full list (capped at 500)
      - by_category: {home: [], service: [...], blog: [...], location: [...], info: [...], page: [...]}
      - inspected: up to N page metas (title, description, h1, h2) — sampled across categories
    """
    domain = normalize_domain(domain)
    if not domain:
        return {"domain": "", "urls": [], "by_category": {}, "inspected": []}

    urls = _fetch_sitemap(domain)
    if not urls:
        # Fallback: just the homepage
        urls = [f"https://{domain}/"]

    by_cat: dict[str, list[str]] = {}
    for u in urls:
        by_cat.setdefault(_categorise_url(u), []).append(u)

    # Sample: 1 home, up to 4 services, 2 blog, 1 location, 2 other
    sample: list[str] = []
    sample.extend((by_cat.get("home") or [])[:1])
    sample.extend((by_cat.get("service") or [])[:4])
    sample.extend((by_cat.get("blog") or [])[:2])
    sample.extend((by_cat.get("location") or [])[:1])
    sample.extend((by_cat.get("page") or [])[:2])
    sample = sample[:max_pages_to_inspect]

    inspected: list[dict[str, Any]] = []
    for u in sample:
        inspected.append(_page_meta(u))

    return {
        "domain":      domain,
        "url_count":   len(urls),
        "urls":        urls,
        "by_category": {k: v[:50] for k, v in by_cat.items()},
        "inspected":   inspected,
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 2:
        print("Usage: python page_crawler.py <domain>", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(crawl_client(sys.argv[1]), indent=2))
