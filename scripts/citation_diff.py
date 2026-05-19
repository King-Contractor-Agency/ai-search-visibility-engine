#!/usr/bin/env python3
"""Diff per-prompt engine results vs prior state, assign Watch/Alert/Launch tiers."""

from __future__ import annotations

from typing import Any

from common import normalize_domain


TIER_ORDER = {"Clear": -1, "Watch": 0, "Alert": 1, "Launch": 2}

ACTION_BY_TIER = {
    "Clear":  "Client is cited across engines. Hold position.",
    "Watch":  "Slipped one slot or lost a single-engine cite. Monitor.",
    "Alert":  "Client absent from some engines or new competitor surged. Brief recommended.",
    "Launch": "Client absent from ALL engines for this prompt. Auto-brief generated.",
}


def _client_present(citations: list[dict[str, Any]], client_domain: str) -> int | None:
    """Return the citation position of the client, or None if absent."""
    if not client_domain:
        return None
    for c in citations:
        if c.get("domain") == client_domain:
            return c.get("position") or 1
    return None


def _matched_competitors(citations: list[dict[str, Any]], competitor_domains: list[str]) -> list[str]:
    found: list[str] = []
    comp_set = {normalize_domain(d) for d in competitor_domains if d}
    for c in citations:
        d = c.get("domain") or ""
        if d in comp_set and d not in found:
            found.append(d)
    return found


def analyse_prompt(
    *,
    client_domain: str,
    competitor_domains: list[str],
    engine_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    engine_results: list of standardised engine outputs (the dict from each engine_*.query()).
    Returns per-prompt summary: tier, presence, competitor map, raw counts.
    """
    engines_with_cite: list[str] = []
    engines_without_cite: list[str] = []
    competitor_hits: dict[str, list[str]] = {}  # competitor_domain -> [engines]
    positions: dict[str, int] = {}              # engine -> client position
    total_engines = 0
    failed: list[str] = []

    for res in engine_results:
        engine = res.get("engine", "?")
        if not res.get("ok"):
            failed.append(engine)
            continue
        total_engines += 1
        citations = res.get("citations") or []
        pos = _client_present(citations, client_domain)
        if pos is not None:
            engines_with_cite.append(engine)
            positions[engine] = pos
        else:
            engines_without_cite.append(engine)
        for comp in _matched_competitors(citations, competitor_domains):
            competitor_hits.setdefault(comp, []).append(engine)

    if total_engines == 0:
        tier = "Watch"
        rationale = "All engines failed — cannot evaluate."
    elif len(engines_with_cite) == 0 and competitor_hits:
        tier = "Launch"
        rationale = f"Client absent from {total_engines} engines; {len(competitor_hits)} competitor(s) cited."
    elif len(engines_with_cite) == 0:
        tier = "Alert"
        rationale = f"Client absent from all {total_engines} engines (no competitors cited either)."
    elif engines_without_cite:
        tier = "Alert"
        rationale = f"Cited in {len(engines_with_cite)}/{total_engines} engines: missing from {', '.join(engines_without_cite)}."
    elif any(p > 3 for p in positions.values()):
        tier = "Watch"
        rationale = "Cited everywhere but position below #3 in at least one engine."
    else:
        tier = "Clear"
        rationale = "Cited in top positions across all engines."

    return {
        "tier":               tier,
        "rationale":          rationale,
        "recommended_action": ACTION_BY_TIER.get(tier, ""),
        "engines_total":      total_engines,
        "engines_cited":      engines_with_cite,
        "engines_missing":    engines_without_cite,
        "engines_failed":     failed,
        "client_positions":   positions,
        "competitor_hits":    competitor_hits,
    }


def hold_tier(prev_tier: str, new_tier: str) -> str:
    """Never-downgrade: if prior tier was hotter, hold it."""
    return prev_tier if TIER_ORDER.get(prev_tier, -1) > TIER_ORDER.get(new_tier, -1) else new_tier


def detect_escalation(prev_tier: str, new_tier: str) -> bool:
    return TIER_ORDER.get(new_tier, -1) > TIER_ORDER.get(prev_tier, -1)
