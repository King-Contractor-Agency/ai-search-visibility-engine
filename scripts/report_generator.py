#!/usr/bin/env python3
"""Synthesize all per-prompt fixes for a single client into ONE markdown action plan.

One file per client per scan: output/reports/<client>/YYYY-MM-DD__action-plan.md
This is what gets auto-published to the client deliverables repo (single file, easy to share).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import ROOT, ensure_parent, today_str


REPORTS_DIR = ROOT / "output/reports"


FIX_TYPE_LABEL = {
    "blog-post":      "📝 New blog post",
    "new-page":       "🆕 New page",
    "page-update":    "✏️ Page update",
    "schema":         "🔧 Schema patch",
    "gbp-update":     "📍 Google Business Profile",
    "internal-link":  "🔗 Internal link build",
}

URGENCY_BADGE = {
    "high":   "🔴 HIGH",
    "medium": "🟡 MED",
    "low":    "🟢 LOW",
}


def _executive_summary(client: dict, fixes: list[dict]) -> str:
    by_type: dict[str, int] = {}
    by_urgency: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for f in fixes:
        spec = f.get("spec") or {}
        by_type[spec.get("fix_type", "page-update")] = by_type.get(spec.get("fix_type", "page-update"), 0) + 1
        u = spec.get("urgency", "medium")
        if u in by_urgency:
            by_urgency[u] += 1

    type_lines = [f"- {FIX_TYPE_LABEL.get(t, t)}: **{n}**" for t, n in sorted(by_type.items(), key=lambda x: -x[1])]
    base = client.get("ai_visibility_baseline") or ""
    baseline_line = f" — Semrush AI Visibility baseline: **{base}/100**" if base else ""

    return (
        f"# AI Visibility Action Plan — {client.get('client_name','')}\n"
        f"\n"
        f"**Scan date:** {today_str()}{baseline_line}\n"
        f"**Domain:** `{client.get('website_domain','')}`\n"
        f"**Primary market:** {client.get('primary_market','')}\n"
        f"\n"
        f"## Executive summary\n"
        f"\n"
        f"- **{len(fixes)} fixes** generated this scan, ready to implement.\n"
        f"- Urgency mix: {URGENCY_BADGE['high']} {by_urgency['high']}  ·  "
        f"{URGENCY_BADGE['medium']} {by_urgency['medium']}  ·  "
        f"{URGENCY_BADGE['low']} {by_urgency['low']}\n"
        f"- Work breakdown:\n"
        + "\n".join("  " + line for line in type_lines)
        + "\n\n"
        f"Each fix below is ready to paste into the CMS, the page source, or the client's GBP. "
        f"No further interpretation needed — copy, paste, ship.\n\n"
        f"---\n"
    )


def _fix_block(idx: int, fix: dict) -> str:
    spec = fix.get("spec") or {}
    prompt = fix.get("prompt") or {}
    impl = fix.get("implementation") or {}
    analysis = fix.get("analysis") or {}

    fix_type = spec.get("fix_type", "page-update")
    label    = FIX_TYPE_LABEL.get(fix_type, fix_type)
    urgency  = URGENCY_BADGE.get(spec.get("urgency", "medium"), "🟡 MED")
    target   = spec.get("target_url", "—")
    diagnosis = spec.get("diagnosis", "")
    competitor = spec.get("competitor_strength", "")

    competitors_now = list((analysis.get("competitor_hits") or {}).keys())[:3]
    comp_str = ", ".join(competitors_now) or "—"

    header = (
        f"## Fix {idx}: {label}  ·  {urgency}\n\n"
        f"**Losing prompt:** _\"{prompt.get('text','')}\"_  \n"
        f"**Engines client is missing from:** {', '.join(analysis.get('engines_missing') or []) or 'none'}  \n"
        f"**Competitors currently winning this prompt:** {comp_str}  \n"
        f"**Target URL:** `{target}`\n\n"
        f"### Diagnosis\n{diagnosis}\n\n"
        f"### Competitor strength\n{competitor or '—'}\n\n"
        f"---\n\n"
        f"### Ready-to-ship implementation\n\n"
    )

    if not impl.get("ok"):
        body = f"_⚠️ Implementation agent failed: {impl.get('error','unknown error')}_\n"
    else:
        body = impl.get("markdown", "_(empty)_")

    return header + body + "\n\n---\n\n"


def build_client_report(client: dict, fixes: list[dict]) -> str:
    if not fixes:
        return (
            f"# AI Visibility Action Plan — {client.get('client_name','')}\n"
            f"\n**Scan date:** {today_str()}\n\n"
            f"No Launch- or Alert-tier prompts were detected for {client.get('client_name','')} this scan. "
            f"The client is holding position across the prompts we tracked. No fixes generated.\n"
        )

    fixes_sorted = sorted(
        fixes,
        key=lambda f: (
            {"high": 0, "medium": 1, "low": 2}.get((f.get("spec") or {}).get("urgency", "medium"), 1),
            (f.get("spec") or {}).get("fix_type", ""),
        ),
    )

    out = _executive_summary(client, fixes_sorted)
    for i, fix in enumerate(fixes_sorted, start=1):
        out += _fix_block(i, fix)
    return out


def write_client_report(client: dict, fixes: list[dict]) -> Path:
    path = REPORTS_DIR / client["client_slug"] / f"{today_str()}__action-plan.md"
    ensure_parent(path)
    path.write_text(build_client_report(client, fixes), encoding="utf-8")
    return path
