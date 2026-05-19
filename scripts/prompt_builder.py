#!/usr/bin/env python3
"""Build buyer-intent prompts per client from templates + service/market matrix."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from common import ROOT, slugify, normalize_domain, parse_bool


CLIENT_TARGETS_CSV  = ROOT / "data/client_targets.csv"
PROMPT_TEMPLATES_CSV = ROOT / "data/prompt_templates.csv"


def load_clients(path: Path = CLIENT_TARGETS_CSV) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not parse_bool(row.get("enabled"), default=True):
                continue
            row["website_domain"]      = normalize_domain(row.get("website_domain", ""))
            row["markets"]             = [m.strip() for m in (row.get("markets") or "").split(";") if m.strip()]
            row["services"]            = [s.strip() for s in (row.get("services") or "").split(";") if s.strip()]
            row["competitors"]         = [normalize_domain(c) for c in (row.get("competitors") or "").split(";") if c.strip()]
            row["deliverables_repo"]   = (row.get("deliverables_repo") or "").strip()
            row["deliverables_branch"] = (row.get("deliverables_branch") or "").strip()
            row["deliverables_path"]   = (row.get("deliverables_path") or "").strip()
            rows.append(row)
    return rows


def load_templates(path: Path = PROMPT_TEMPLATES_CSV) -> list[dict]:
    out: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                row["weight"] = int(row.get("weight") or 1)
            except ValueError:
                row["weight"] = 1
            out.append(row)
    return out


def _pick_service(services: list[str], template_id: str) -> str:
    """Most templates use a single dominant service; storm/hail templates pin to that one."""
    if not services:
        return "roofing"
    if "storm" in template_id:
        for s in services:
            if "storm" in s.lower():
                return s
    if "hail" in template_id:
        for s in services:
            if "hail" in s.lower():
                return s
    return services[0]


def _format_template(template_text: str, *, market: str, service: str, company_name: str) -> str:
    """str.format with safe fallbacks for unknown placeholders."""
    try:
        return template_text.format(market=market, service=service, company_name=company_name)
    except (KeyError, IndexError):
        return template_text


def expand_for_client(client: dict, templates: list[dict], limit: int = 0) -> list[dict]:
    """Return a sorted, weighted list of prompts for this client. limit=0 means all.

    Templates marked scope=branded fire ONCE per client using the primary_market.
    Templates marked scope=market (or blank) fire once per market.
    """
    prompts: list[dict] = []
    company_name = client.get("company_name") or client.get("client_name") or ""
    primary_market = client.get("primary_market") or (client["markets"][0] if client["markets"] else "")

    for template in templates:
        scope   = (template.get("scope") or "market").strip().lower()
        service = _pick_service(client["services"], template["template_id"])

        if scope == "branded":
            markets_iter = [primary_market]
        else:
            markets_iter = client["markets"]

        for market in markets_iter:
            text = _format_template(
                template["template"],
                market=market,
                service=service,
                company_name=company_name,
            )
            prompts.append({
                "prompt_id":   slugify(f"{client['client_slug']}__{template['template_id']}__{market}"),
                "client_slug": client["client_slug"],
                "template_id": template["template_id"],
                "intent":      template.get("intent") or "",
                "scope":       scope,
                "market":      market,
                "service":     service,
                "company_name":company_name,
                "text":        text,
                "weight":      template["weight"],
            })
    prompts.sort(key=lambda p: (-p["weight"], 0 if p["scope"] == "branded" else 1, p["market"], p["template_id"]))
    if limit and len(prompts) > limit:
        prompts = prompts[:limit]
    return prompts


def expand_all(clients: list[dict] | None = None, templates: list[dict] | None = None, limit: int = 0) -> Iterator[dict]:
    clients   = clients   if clients   is not None else load_clients()
    templates = templates if templates is not None else load_templates()
    for client in clients:
        for prompt in expand_for_client(client, templates, limit=limit):
            yield prompt


if __name__ == "__main__":
    # CLI preview: print counts and a sample.
    clients   = load_clients()
    templates = load_templates()
    total = 0
    for client in clients:
        prompts = expand_for_client(client, templates)
        total += len(prompts)
        print(f"{client['client_slug']:30s}  {len(prompts):4d} prompts  · {client['website_domain']}")
        for p in prompts[:2]:
            print(f"     · {p['text']}")
    print(f"\nTotal prompts across portfolio: {total}")
