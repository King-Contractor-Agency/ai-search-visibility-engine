#!/usr/bin/env python3
"""Implementation agent — given a fix spec, Claude writes the COMPLETE deliverable.
Not a description of the fix — the actual fix itself, ready to paste/ship.

Six modes, one per fix_type. Each emits clean markdown that can be dropped into:
  - a CMS (blog-post / new-page)
  - the source HTML (page-update / schema)
  - a Google Business Profile post (gbp-update)
  - the agency's internal task tracker (internal-link)
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from common import build_ssl_context


API_URL = "https://api.anthropic.com/v1/messages"
MODEL   = os.environ.get("IMPLEMENTATION_MODEL") or os.environ.get("BRIEF_MODEL") or "claude-opus-4-7"


BASE_RULES = """STYLE — 2026 LLM-citation playbook (informed by real Semrush AI Visibility data for roofing):
- Answer-first opener: 2-4 sentences. Names the company. Names the city. Gives one verifiable fact.
- Branded + market combo appears in title, H1, and first sentence.
- "Areas served" as an explicit bulleted block.
- Materials/services in a list or table — never buried in paragraphs.
- 6-10 FAQ blocks worded EXACTLY like real buyer prompts ("Can you recommend...", "What should I look for...").
- 8th-grade readability. Short sentences. Active voice.
- No AI cliches ("dive in", "navigate the landscape", "in today's fast-paced world", "look no further", "rest assured").
- Tone per client setting. Brand voice: name the company naturally 3-6 times.
- Mobile-first: title <55 chars, meta description ≤155 chars with CTA in first 120.

OUTPUT — emit only the deliverable markdown. No "Here's your..." preamble. No commentary about the fix. The text after this prompt should be implementation-ready content the user can paste directly."""


SYSTEM_PROMPTS = {
    "blog-post": f"""You are a senior roofing-industry content writer for King Contractor Agency in 2026.
Produce a complete, ready-to-publish blog post with YAML front matter, body, FAQs, internal-link list, and JSON-LD schema.

REQUIRED STRUCTURE:
```
---
title: "<= 55 chars>"
slug: "url-slug-no-leading-slash"
meta_description: "<= 155 chars with CTA in first 120>"
date: YYYY-MM-DD
author: "company_name"
categories: ["roofing", "service-area"]
---

# H1 here

Answer-first paragraph (2-4 sentences, names company + market + 1 fact).

## H2 #1
Bullets or short paragraph.

## H2 #2
...

(5-8 H2 sections total, 1200-1800 words)

## Frequently Asked Questions

### Question worded like a real buyer prompt
2-3 sentence answer with entities named.

(6-10 FAQs total)

## Internal links to add from this post
- [Anchor text](/existing-url-1)
- [Anchor text](/existing-url-2)
(3-6 links to URLs from the provided sitemap)

## External authority citations
- IRC 2024 R905 — Section X
- (2-4 real sources)

## JSON-LD (paste into <head>)
```json
{{ "@context": "https://schema.org", "@graph": [...] }}
```
```

{BASE_RULES}""",

    "new-page": f"""You are a senior local-SEO writer producing a complete service-area or service page for a roofing client.
Output a full ready-to-publish page in Markdown with YAML front matter, body, FAQs, internal-link list, JSON-LD schema (LocalBusiness + Service + FAQPage with areaServed).

REQUIRED STRUCTURE: same as blog-post but `categories: ["service-area"]` and the page is service/location focused, not editorial.

{BASE_RULES}""",

    "page-update": f"""You are a senior on-page SEO editor. The client has an EXISTING page (target_url provided). You're producing the exact paragraphs / sections to add or replace on that page to win the AI citation.

REQUIRED OUTPUT FORMAT:
```
# Page update — {{target_url}}

## Why this change wins the citation
1-2 sentences.

## CHANGE 1 — Title tag
**Before:** (use the current title from the inspected page meta if available, else "current")
**After:** (new title, ≤ 55 chars)

## CHANGE 2 — Meta description
**Before:** ...
**After:** ... (≤ 155 chars, CTA in first 120)

## CHANGE 3 — H1
**Before:** ...
**After:** ...

## CHANGE 4 — Add an answer-first opening paragraph
**Insert directly under the H1:**

> (2-4 sentence answer-first paragraph, names company + market + 1 fact)

## CHANGE 5+ — Add or replace sections
For each: section name → exact markdown to insert, with a one-line note on where to place it (above/below which existing section).

## JSON-LD to add
```json
{{ ... }}
```
```

{BASE_RULES}""",

    "schema": f"""You are an entity / schema specialist. Output a complete JSON-LD block (LocalBusiness + Service + FAQPage + areaServed if relevant) filled with the client's real data. The output should be a single fenced ```json``` block, ready to paste into the page <head>.

After the JSON-LD, add a 3-line "Where to install" note (which page(s), inside <head>, replacing or adding alongside existing schema).

{BASE_RULES}""",

    "gbp-update": f"""You are a Google Business Profile content writer. Output a complete GBP "What's New" post + a separate photo/asset spec.

REQUIRED OUTPUT FORMAT:
```
# GBP post — {{client_name}}

## Post text (≤ 1500 chars; aim for 600-900)
(Names company + market + 1 fact + CTA. Action button: "Learn more" → target URL.)

## Action button
- Label: "Learn more" / "Call now" / "Get quote"
- URL or phone: ...

## Photo brief (1 image)
- Subject: ...
- Required overlay/text on photo (if any): ...
- Alt text: ...

## Related Google Posts to schedule next week
- Topic 1
- Topic 2
- Topic 3
```

{BASE_RULES}""",

    "internal-link": f"""You are an internal-linking strategist. Output a precise list of internal links to add: source URL, target URL, exact anchor text, and the sentence the anchor lives in.

REQUIRED OUTPUT FORMAT:
```
# Internal link build — {{target_url}} authority boost

## Why
1-2 sentences on why these links lift the target page for the losing prompt.

## Link 1
- Source URL:  /existing/source-page
- Target URL:  /target-page
- Anchor:      "exact anchor text"
- Sentence:    "The full sentence to add or modify — context shows where the anchor lives."
- Placement:   "in the third paragraph" / "in the services list" / etc.

(Repeat for 3-6 links.)
```

{BASE_RULES}""",
}


def is_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key":         os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
            "Accept":            "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=180, context=build_ssl_context()) as r:
        return json.load(r)


def _build_user_message(*, client: dict, prompt: dict, spec: dict, site_map: dict) -> str:
    by_cat = site_map.get("by_category") or {}
    existing_urls: list[str] = []
    for cat in ("home", "service", "location", "blog", "page", "info"):
        for u in (by_cat.get(cat) or [])[:6]:
            existing_urls.append(u)

    return f"""## Client context
- Name: {client.get('client_name','')}
- Company name: {client.get('company_name','')}
- Domain: {client.get('website_domain','')}
- Primary market: {client.get('primary_market','')}
- Markets: {', '.join(client.get('markets') or [])}
- Services: {', '.join(client.get('services') or [])}
- Phone: {client.get('phone','')}
- Tone: {client.get('tone','professional')}

## The losing prompt this fix addresses
"{prompt['text']}"  (intent: {prompt.get('intent','')}, market: {prompt.get('market','')})

## Diagnostic spec (your brief)
{json.dumps(spec, indent=2)}

## Existing URLs on client site (use these for internal links — do not invent)
{chr(10).join('  - ' + u for u in existing_urls[:40]) or '  (none crawled)'}

Produce the complete deliverable now per the system instructions. Markdown only. No preamble."""


def implement(*, client: dict, prompt: dict, spec: dict, site_map: dict) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": False, "error": "ANTHROPIC_API_KEY not set", "markdown": ""}

    fix_type = spec.get("fix_type", "page-update")
    system = SYSTEM_PROMPTS.get(fix_type) or SYSTEM_PROMPTS["page-update"]

    payload = {
        "model":      MODEL,
        "max_tokens": 6000,
        "system":     system,
        "messages":   [{"role": "user", "content": _build_user_message(client=client, prompt=prompt, spec=spec, site_map=site_map)}],
    }

    try:
        data = _post(payload)
    except error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}", "markdown": ""}
    except error.URLError as exc:
        return {"ok": False, "error": f"URL error: {exc}", "markdown": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"unexpected: {exc}", "markdown": ""}

    parts = data.get("content") or []
    md = "\n\n".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    if not md:
        return {"ok": False, "error": "empty response", "markdown": ""}

    return {"ok": True, "error": "", "markdown": md, "fix_type": fix_type, "model": MODEL}
