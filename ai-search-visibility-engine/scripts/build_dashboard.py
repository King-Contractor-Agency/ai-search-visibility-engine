#!/usr/bin/env python3
"""Compile per-day scan results into docs/data.json for the GH Pages dashboard."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import ROOT, ensure_parent, load_json_file, now_iso, today_str


HISTORY_DIR = ROOT / "data/prompt_history"
DOCS_DATA = ROOT / "docs/data.json"
CITATIONS_CSV = ROOT / "output/citations.csv"


def _read_history_days(max_days: int = 30) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    if not HISTORY_DIR.exists():
        return days
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:max_days]
    for f in files:
        try:
            data = load_json_file(f, None)
            if data:
                days.append(data)
        except Exception:  # noqa: BLE001
            continue
    return days


def _portfolio_trend(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for day in reversed(days):  # oldest → newest
        counts = day.get("tier_counts", {})
        out.append({
            "date":   day.get("date") or "",
            "Clear":  counts.get("Clear", 0),
            "Watch":  counts.get("Watch", 0),
            "Alert":  counts.get("Alert", 0),
            "Launch": counts.get("Launch", 0),
            "scanned": day.get("prompts_scanned", 0),
        })
    return out


def _per_client_rollup(today: dict[str, Any]) -> list[dict[str, Any]]:
    by_client: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "client_slug":   "",
        "client_name":   "",
        "website_domain":"",
        "tier_counts":   {"Clear": 0, "Watch": 0, "Alert": 0, "Launch": 0},
        "prompts":       [],
        "competitor_share": {},
        "engine_presence": {"perplexity":0,"chatgpt":0,"gemini":0,"google_aio":0},
        "engine_total":    {"perplexity":0,"chatgpt":0,"gemini":0,"google_aio":0},
    })
    for prompt in today.get("prompts", []):
        slug = prompt.get("client_slug", "")
        bucket = by_client[slug]
        bucket["client_slug"]    = slug
        bucket["client_name"]    = prompt.get("client_name", "")
        bucket["website_domain"] = prompt.get("client_domain", "")
        bucket["tier_counts"][prompt.get("tier", "Watch")] = bucket["tier_counts"].get(prompt.get("tier", "Watch"), 0) + 1
        bucket["prompts"].append({
            "prompt_id":  prompt.get("prompt_id", ""),
            "text":       prompt.get("text", ""),
            "market":     prompt.get("market", ""),
            "service":    prompt.get("service", ""),
            "tier":       prompt.get("tier", ""),
            "rationale":  prompt.get("rationale", ""),
            "engines_cited":   prompt.get("engines_cited", []),
            "engines_missing": prompt.get("engines_missing", []),
            "competitor_hits": prompt.get("competitor_hits", {}),
            "brief_path":      prompt.get("brief_path", ""),
        })
        for comp in (prompt.get("competitor_hits") or {}).keys():
            bucket["competitor_share"][comp] = bucket["competitor_share"].get(comp, 0) + 1
        for engine in prompt.get("engines_cited") or []:
            bucket["engine_presence"][engine] = bucket["engine_presence"].get(engine, 0) + 1
        for engine in (prompt.get("engines_cited") or []) + (prompt.get("engines_missing") or []):
            bucket["engine_total"][engine] = bucket["engine_total"].get(engine, 0) + 1

    return sorted(by_client.values(), key=lambda b: (-b["tier_counts"]["Launch"], -b["tier_counts"]["Alert"], b["client_name"]))


def _briefs_index(today: dict[str, Any]) -> list[dict[str, Any]]:
    """Now returns the per-client action-plan reports (one row per client)."""
    out = []
    for r in today.get("reports", []) or []:
        out.append({
            "client_slug":   r.get("client_slug", ""),
            "client_name":   r.get("client_name", ""),
            "prompt_text":   f"{r.get('fixes_count', 0)} fixes — full action plan",
            "tier":          "Action Plan",
            "market":        "",
            "path":          r.get("path", ""),
            "published_url": r.get("published_url", ""),
            "generated_at":  today.get("generated_at", ""),
        })
    return out


def append_citations_csv(today: dict[str, Any]) -> None:
    """Append a flat row per (prompt, engine) to output/citations.csv."""
    if not today.get("prompts"):
        return
    ensure_parent(CITATIONS_CSV)
    write_header = not CITATIONS_CSV.exists()
    fields = [
        "date", "client_slug", "client_name", "prompt_id", "prompt_text",
        "market", "service", "engine", "client_cited", "client_position",
        "tier", "competitors_cited",
    ]
    with CITATIONS_CSV.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        date = today.get("date", today_str())
        for prompt in today["prompts"]:
            comp_summary = "; ".join(prompt.get("competitor_hits", {}).keys())
            engines_cited = set(prompt.get("engines_cited") or [])
            for engine in (prompt.get("engines_cited") or []) + (prompt.get("engines_missing") or []):
                writer.writerow({
                    "date":              date,
                    "client_slug":       prompt.get("client_slug", ""),
                    "client_name":       prompt.get("client_name", ""),
                    "prompt_id":         prompt.get("prompt_id", ""),
                    "prompt_text":       prompt.get("text", ""),
                    "market":            prompt.get("market", ""),
                    "service":           prompt.get("service", ""),
                    "engine":            engine,
                    "client_cited":      "yes" if engine in engines_cited else "no",
                    "client_position":   (prompt.get("client_positions") or {}).get(engine, ""),
                    "tier":              prompt.get("tier", ""),
                    "competitors_cited": comp_summary,
                })


def build(today: dict[str, Any]) -> dict[str, Any]:
    days = _read_history_days(30)
    if today and today.get("date") and (not days or days[0].get("date") != today["date"]):
        days = [today, *days]

    return {
        "generated_at":     now_iso(),
        "today":            today.get("date") or today_str(),
        "tier_counts":      today.get("tier_counts", {"Clear":0,"Watch":0,"Alert":0,"Launch":0}),
        "prompts_scanned":  today.get("prompts_scanned", 0),
        "engines_used":     today.get("engines_used", []),
        "briefs_generated": today.get("briefs_generated", 0),
        "portfolio_trend":  _portfolio_trend(days),
        "clients":          _per_client_rollup(today),
        "briefs":           _briefs_index(today),
        "escalations":      today.get("escalations", []),
    }


def write_dashboard(today: dict[str, Any]) -> Path:
    payload = build(today)
    ensure_parent(DOCS_DATA)
    DOCS_DATA.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    append_citations_csv(today)
    return DOCS_DATA
