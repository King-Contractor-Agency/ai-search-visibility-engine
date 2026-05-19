#!/usr/bin/env python3
"""Diagnostic agent — for a single losing prompt, Claude analyzes WHY the client
isn't being cited and emits a structured fix spec (one of 6 fix types).

Returns JSON, not markdown. The implementation agent consumes this.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, request

from common import build_ssl_context


API_URL = "https://api.anthropic.com/v1/messages"
MODEL   = os.environ.get("DIAGNOSTIC_MODEL") or os.environ.get("BRIEF_MODEL") or "claude-sonnet-4-5"


SYSTEM_PROMPT = """You are a senior AEO/SEO diagnostician for King Contractor Agency in 2026.
You analyze why a specific roofing client isn't being cited by AI search engines (ChatGPT, Gemini, Google AI Overviews, Google AI Mode) for a given buyer-intent prompt.

You ONLY output a single JSON object — no preamble, no markdown fences, no commentary. Just `{...}`.

Required schema:
{
  "diagnosis":            "2-3 sentence root cause analysis",
  "fix_type":             one of: "blog-post" | "page-update" | "new-page" | "schema" | "gbp-update" | "internal-link",
  "target_url":           "existing client URL if page-update/internal-link; proposed slug like '/services/hail-damage-littleton-co' if new-page or blog-post",
  "title":                "<= 55 chars, includes company OR market + intent",
  "meta_description":     "<= 155 chars, includes a CTA in first 120",
  "h1":                   "the on-page H1",
  "required_sections":    ["H2 section names — 5 to 8 strings"],
  "must_include_entities":["company_name", "primary_market", "service", and any specific cities the prompt targets"],
  "must_include_facts":   ["2-5 verifiable facts the page must contain — certifications, years in business, materials handled, code references, etc."],
  "must_cite_authorities":["2-4 high-trust external sources to cite — only ones that actually exist, like 'IRC 2024 R905', 'NWS storm climatology', 'IBHS FORTIFIED Roof', 'GAF Master Elite certification'"],
  "competitor_strength":  "1-2 sentence summary of what the winning competitor(s) are doing that the client isn't",
  "internal_links_to_use":["3-6 existing client URLs (from the sitemap provided) to link from this fix"],
  "urgency":              "low" | "medium" | "high",
  "estimated_words":      integer 800-2000 for blog-post/new-page; 200-500 for page-update; 50-200 for gbp-update; 0 for schema/internal-link
}

Fix-type selection rules:
- prompt is branded ("{Company} services") and competitor is cited but client isn't → fix_type=page-update on home or about page
- prompt is local-commercial ("best roofers in {City}") and client has no service-area page for that city → fix_type=new-page
- prompt is educational ("common roofing materials in {City}") and client has a relevant blog but it's not winning → fix_type=blog-post
- prompt asks about reviews/ratings → fix_type=gbp-update (post + photo prompt; the actual review work is offline)
- client cited but no clickable link in citations → fix_type=schema (LocalBusiness + Service + areaServed)
- client is mentioned but ranked below #3 → fix_type=internal-link (boost authority to existing page)

Do not invent URLs. target_url must either match an existing URL from the provided sitemap, or be a proposed new slug starting with `/`."""


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
    with request.urlopen(req, timeout=90, context=build_ssl_context()) as r:
        return json.load(r)


def _build_user_message(
    *,
    client: dict,
    prompt: dict,
    analysis: dict,
    engine_results: list[dict],
    site_map: dict,
) -> str:
    engine_blocks: list[str] = []
    for res in engine_results:
        if not res.get("ok"):
            engine_blocks.append(f"### {res['engine']} — FAILED ({res.get('error','')})")
            continue
        cites = res.get("citations") or []
        cite_lines = [f"  {i+1}. {c.get('domain','?')} — {c.get('url','')}" for i, c in enumerate(cites[:8])]
        engine_blocks.append(
            f"### {res['engine']} ({res.get('model','')})\n"
            f"Answer excerpt:\n{(res.get('answer') or '')[:900]}\n\n"
            f"Cited sources:\n" + ("\n".join(cite_lines) or "  (none)")
        )

    competitors = list((analysis.get("competitor_hits") or {}).keys())
    by_cat = site_map.get("by_category") or {}
    site_lines: list[str] = []
    for cat in ("home", "service", "location", "blog", "page", "info"):
        urls = by_cat.get(cat) or []
        if urls:
            site_lines.append(f"  {cat} ({len(urls)}):")
            for u in urls[:8]:
                site_lines.append(f"    - {u}")
    inspected_lines: list[str] = []
    for p in site_map.get("inspected") or []:
        if not p.get("ok"):
            continue
        inspected_lines.append(f"- {p['url']}")
        inspected_lines.append(f"    title: {p.get('title','')[:140]}")
        inspected_lines.append(f"    desc:  {p.get('meta_description','')[:140]}")
        h1s = p.get("h1") or []
        if h1s: inspected_lines.append(f"    H1:    {h1s[0][:140]}")

    return f"""## Client
- Name: {client.get('client_name','')}
- Company name (as AI engines write it): {client.get('company_name','')}
- Domain: {client.get('website_domain','')}
- Primary market: {client.get('primary_market','')}
- All markets: {', '.join(client.get('markets') or [])}
- Services: {', '.join(client.get('services') or [])}
- Tone: {client.get('tone','professional')}
- Phone: {client.get('phone','')}
- Semrush AI Visibility baseline: {client.get('ai_visibility_baseline','') or 'unknown'}/100

## Losing Prompt
"{prompt['text']}"
- Intent: {prompt.get('intent','')}
- Scope:  {prompt.get('scope','')}
- Target market:  {prompt.get('market','')}
- Target service: {prompt.get('service','')}

## Why it's losing (analyzer output)
- Tier: {analysis['tier']}
- {analysis['rationale']}
- Client cited by: {', '.join(analysis.get('engines_cited') or []) or 'none'}
- Client missing from: {', '.join(analysis.get('engines_missing') or []) or 'none'}
- Competitors currently cited: {', '.join(competitors) or 'none'}

## What each engine actually said
{chr(10).join(engine_blocks) or '(no engine data)'}

## Client's existing site map ({site_map.get('url_count',0)} URLs)
{chr(10).join(site_lines) or '  (sitemap not accessible)'}

## Sampled existing page metas
{chr(10).join(inspected_lines) or '  (no pages crawled)'}

Emit the diagnostic JSON now. No preamble. No fences. Just `{{...}}`."""


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # strip code fences if Claude wrapped despite instructions
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first {...} block
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def is_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def diagnose(*, client: dict, prompt: dict, analysis: dict, engine_results: list[dict], site_map: dict) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": False, "error": "ANTHROPIC_API_KEY not set", "spec": None}

    payload = {
        "model":      MODEL,
        "max_tokens": 2000,
        "system":     SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": _build_user_message(
            client=client, prompt=prompt, analysis=analysis,
            engine_results=engine_results, site_map=site_map)}],
    }

    try:
        data = _post(payload)
    except error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}", "spec": None}
    except error.URLError as exc:
        return {"ok": False, "error": f"URL error: {exc}", "spec": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"unexpected: {exc}", "spec": None}

    parts = data.get("content") or []
    text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    spec = _extract_json(text)
    if not spec:
        return {"ok": False, "error": "could not parse diagnostic JSON", "spec": None, "raw": text[:500]}

    return {"ok": True, "error": "", "spec": spec, "model": MODEL}
