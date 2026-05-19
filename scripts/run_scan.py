#!/usr/bin/env python3
"""
AI Search Visibility — orchestrator.

Pipeline:
  1. SCAN     — fire prompts at ChatGPT + Gemini, capture citations
  2. CRAWL    — for each client with losing prompts, fetch their sitemap once
  3. DIAGNOSE — Claude (concurrent) analyses each Launch/Alert prompt → fix spec JSON
  4. IMPLEMENT — Claude (concurrent) writes the actual ready-to-ship deliverable
  5. REPORT   — one consolidated markdown action plan per client per scan
  6. PUBLISH  — push that single report file to the client's deliverables repo (optional)
  7. DASHBOARD — write docs/data.json + docs/latest_summary.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import agent_diagnostic
import agent_implementation
import citation_diff
import engine_gemini
import engine_openai
import page_crawler
import publish_briefs
import report_generator
from build_dashboard import write_dashboard
from common import (
    ROOT,
    load_env_file,
    load_json_file,
    now_iso,
    parse_bool,
    today_str,
    write_json_file,
)
from prompt_builder import expand_for_client, load_clients, load_templates


SEEN_PATH         = ROOT / "data/seen_prompts.json"
ACTIVE_TIERS_PATH = ROOT / "data/active_tiers.json"
HISTORY_DIR       = ROOT / "data/prompt_history"
SUMMARY_PATH      = ROOT / "docs/latest_summary.md"

ENGINES = [engine_openai, engine_gemini]

# Concurrency. Anthropic supports plenty; engine APIs we keep modest.
ENGINE_WORKERS = 4
AGENT_WORKERS  = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Search Visibility scanner")
    parser.add_argument("--clients", default="", help="Comma-separated client_slug filter (overrides env)")
    parser.add_argument("--prompt-limit", type=int, default=0)
    parser.add_argument("--engines", default="")
    parser.add_argument("--no-agents", action="store_true", help="Skip diagnostic + implementation agents (scan only)")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


# ── Phase 1: SCAN ─────────────────────────────────────────────────────────

def _engine_filter(args_engines: str) -> list:
    if args_engines:
        wanted = {e.strip() for e in args_engines.split(",") if e.strip()}
        return [e for e in ENGINES if e.ENGINE in wanted]
    return list(ENGINES)


def _query_one_engine(mod, prompt_text: str) -> dict[str, Any]:
    if not mod.is_enabled():
        if parse_bool(os.environ.get("SKIP_MISSING_ENGINES"), default=True):
            return None  # caller filters out
        return {"engine": mod.ENGINE, "model": getattr(mod, "MODEL", ""), "ok": False, "error": "api_key_missing", "answer": "", "citations": []}
    try:
        return mod.query(prompt_text)
    except Exception as exc:  # noqa: BLE001
        return {"engine": mod.ENGINE, "model": getattr(mod, "MODEL", ""), "ok": False, "error": f"raised: {exc}", "answer": "", "citations": []}


def _run_engines_for_prompt(prompt_text: str, engines: list) -> list[dict[str, Any]]:
    """Engines in parallel for a single prompt."""
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=ENGINE_WORKERS) as pool:
  futures = {pool.submit(_query_one_engine, mod, prompt_text): mod for mod in engines}
    for f in as_completed(futures):
            r = f.result()
            if r is not None:
                if not r.get("ok"):
                    print(f"    [engine] {r.get('engine')} FAILED: {(r.get('error') or '')[:300]}", file=sys.stderr)
                out.append(r)
        return out


# ── Phase 3/4: DIAGNOSE + IMPLEMENT ──────────────────────────────────────

def _diagnose_and_implement(*, client: dict, prompt: dict, analysis: dict, engine_results: list[dict], site_map: dict) -> dict[str, Any]:
    """Run diagnostic, then implementation, for a single losing prompt."""
    diag = agent_diagnostic.diagnose(
        client=client, prompt=prompt, analysis=analysis,
        engine_results=engine_results, site_map=site_map,
    )
    if not diag.get("ok"):
        return {
            "client_slug":  client["client_slug"],
            "prompt":       prompt,
            "analysis":     analysis,
            "spec":         None,
            "implementation": {"ok": False, "error": diag.get("error", "diagnostic failed"), "markdown": ""},
            "engine_results": engine_results,
        }

    impl = agent_implementation.implement(
        client=client, prompt=prompt, spec=diag["spec"], site_map=site_map,
    )
    return {
        "client_slug":  client["client_slug"],
        "prompt":       prompt,
        "analysis":     analysis,
        "spec":         diag["spec"],
        "implementation": impl,
        "engine_results": engine_results,
    }


# ── Main ─────────────────────────────────────────────────────────────────

def _client_filter_from_args(args: argparse.Namespace) -> set[str]:
    raw = args.clients or os.environ.get("CLIENTS_FILTER", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def _write_summary_md(snapshot: dict[str, Any]) -> None:
    counts = snapshot["tier_counts"]
    lines = [
        f"# AI Search Visibility — {snapshot['date']}",
        "",
        f"**Engines used:** {', '.join(snapshot['engines_used']) or 'none'}",
        f"**Prompts scanned:** {snapshot['prompts_scanned']}",
        f"**Reports generated:** {len(snapshot.get('reports', []))} client action plans",
        f"**Fixes generated:** {snapshot.get('fixes_generated', 0)}",
        "",
        "## Tier counts",
        f"- 🔴 Launch: **{counts.get('Launch',0)}**",
        f"- 🟡 Alert:  **{counts.get('Alert',0)}**",
        f"- 🟢 Watch:  **{counts.get('Watch',0)}**",
        f"- 🔵 Clear:  **{counts.get('Clear',0)}**",
        "",
    ]
    reports = snapshot.get("reports") or []
    if reports:
        lines.append("## Per-client action plans")
        for r in reports:
            lines.append(f"- **{r['client_name']}** — {r['fixes_count']} fixes — `{r['path']}`")
            if r.get("published_url"):
                lines.append(f"  - Published: {r['published_url']}")
        lines.append("")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    load_env_file()
    args = parse_args()

    engines = _engine_filter(args.engines)
    if not engines:
        print("No engines enabled.", file=sys.stderr); return 1

    clients = load_clients()
    cfilter = _client_filter_from_args(args)
    if cfilter:
        clients = [c for c in clients if c["client_slug"] in cfilter]
    if not clients:
        print("No enabled clients to scan.", file=sys.stderr); return 1

    templates = load_templates()
    prompt_limit = args.prompt_limit or int(os.environ.get("PROMPT_LIMIT") or 0)

    seen_store: dict[str, Any] = load_json_file(SEEN_PATH, {})
    active_tiers: dict[str, Any] = load_json_file(ACTIVE_TIERS_PATH, {})

    today = today_str()
    snapshot: dict[str, Any] = {
        "date":             today,
        "generated_at":     now_iso(),
        "engines_used":     [m.ENGINE for m in engines if m.is_enabled()],
        "tier_counts":      {"Clear": 0, "Watch": 0, "Alert": 0, "Launch": 0},
        "prompts":          [],
        "escalations":      [],
        "fixes_generated":  0,
        "reports":          [],
    }

    # ── PHASE 1 + 2: scan engines per prompt; collect losing prompts per client ──
    print(f"[phase 1] scanning {len(clients)} client(s) with engines: {snapshot['engines_used']}", file=sys.stderr)
    losing_by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    engine_results_by_prompt: dict[str, list[dict[str, Any]]] = {}

    for client in clients:
        prompts = expand_for_client(client, templates, limit=prompt_limit)
        print(f"  · {client['client_slug']}: {len(prompts)} prompts", file=sys.stderr)
        for prompt in prompts:
            engine_results = _run_engines_for_prompt(prompt["text"], engines)
            engine_results_by_prompt[prompt["prompt_id"]] = engine_results

            analysis = citation_diff.analyse_prompt(
                client_domain=client["website_domain"],
                competitor_domains=client["competitors"],
                engine_results=engine_results,
            )

            prev_tier = active_tiers.get(prompt["prompt_id"], {}).get("tier", "Clear")
            held_tier = citation_diff.hold_tier(prev_tier, analysis["tier"])
            escalated = citation_diff.detect_escalation(prev_tier, analysis["tier"])
            analysis["tier"] = held_tier

            prompt_record = {
                "prompt_id":        prompt["prompt_id"],
                "client_slug":      client["client_slug"],
                "client_name":      client["client_name"],
                "client_domain":    client["website_domain"],
                "template_id":      prompt["template_id"],
                "intent":           prompt["intent"],
                "scope":            prompt.get("scope", ""),
                "market":           prompt["market"],
                "service":          prompt["service"],
                "text":             prompt["text"],
                "tier":             held_tier,
                "previous_tier":    prev_tier,
                "rationale":        analysis["rationale"],
                "engines_cited":    analysis["engines_cited"],
                "engines_missing":  analysis["engines_missing"],
                "engines_failed":   analysis["engines_failed"],
                "engines_total":    analysis["engines_total"],
                "client_positions": analysis["client_positions"],
                "competitor_hits":  analysis["competitor_hits"],
                "brief_path":       "",
                "generated_at":     now_iso(),
            }
            snapshot["prompts"].append(prompt_record)
            snapshot["tier_counts"][held_tier] = snapshot["tier_counts"].get(held_tier, 0) + 1

            if escalated or held_tier in {"Alert", "Launch"}:
                snapshot["escalations"].append({
                    "client_name": client["client_name"],
                    "client_slug": client["client_slug"],
                    "prompt_id":   prompt["prompt_id"],
                    "prompt_text": prompt["text"],
                    "tier":        held_tier,
                    "previous_tier": prev_tier,
                    "rationale":   analysis["rationale"],
                    "engines_cited":   analysis["engines_cited"],
                    "engines_missing": analysis["engines_missing"],
                    "competitor_hits": analysis["competitor_hits"],
                    "brief_path":      "",
                })
                losing_by_client[client["client_slug"]].append({
                    "client":         client,
                    "prompt":         prompt,
                    "analysis":       analysis,
                    "engine_results": engine_results,
                })

            active_tiers[prompt["prompt_id"]] = {"tier": held_tier, "last_seen": now_iso(), "client_slug": client["client_slug"]}
            seen_store[prompt["prompt_id"]] = {"last_tier": held_tier, "last_seen": now_iso(), "client_slug": client["client_slug"]}

    snapshot["prompts_scanned"] = len(snapshot["prompts"])

    # ── PHASE 2: crawl client sitemaps (only those with losing prompts) ──
    print(f"[phase 2] crawling sitemaps for {len(losing_by_client)} client(s) with losing prompts", file=sys.stderr)
    site_maps: dict[str, dict[str, Any]] = {}
    if not args.no_agents and not args.dry_run:
        for client in clients:
            if client["client_slug"] not in losing_by_client:
                continue
            try:
                site_maps[client["client_slug"]] = page_crawler.crawl_client(client["website_domain"])
                print(f"  · {client['client_slug']}: {site_maps[client['client_slug']].get('url_count',0)} URLs", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"  · {client['client_slug']}: crawl failed: {exc}", file=sys.stderr)
                site_maps[client["client_slug"]] = {"domain": client["website_domain"], "urls": [], "by_category": {}, "inspected": []}

    # ── PHASE 3+4: diagnose + implement (concurrent) ──
    fixes_by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not args.no_agents and not args.dry_run and agent_diagnostic.is_enabled():
        total_jobs = sum(len(v) for v in losing_by_client.values())
        print(f"[phase 3+4] diagnose + implement: {total_jobs} agent jobs across {AGENT_WORKERS} workers", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=AGENT_WORKERS) as pool:
            futures = []
            for slug, items in losing_by_client.items():
                site_map = site_maps.get(slug) or {"by_category": {}, "inspected": []}
                for item in items:
                    futures.append(pool.submit(
                        _diagnose_and_implement,
                        client=item["client"], prompt=item["prompt"],
                        analysis=item["analysis"], engine_results=item["engine_results"],
                        site_map=site_map,
                    ))
            for i, f in enumerate(as_completed(futures), start=1):
                result = f.result()
                fixes_by_client[result["client_slug"]].append(result)
                if result.get("implementation", {}).get("ok"):
                    snapshot["fixes_generated"] += 1
                print(f"  · agent {i}/{len(futures)} done ({result['client_slug']})", file=sys.stderr)
                time.sleep(0.1)
    elif args.no_agents:
        print("[phase 3+4] skipped (--no-agents)", file=sys.stderr)
    elif args.dry_run:
        print("[phase 3+4] skipped (--dry-run)", file=sys.stderr)
    else:
        print("[phase 3+4] skipped (ANTHROPIC_API_KEY missing)", file=sys.stderr)

    # ── PHASE 5: per-client report ──
    print(f"[phase 5] writing action plans for {len(fixes_by_client)} client(s)", file=sys.stderr)
    client_lookup = {c["client_slug"]: c for c in clients}
    for slug, fixes in fixes_by_client.items():
        client = client_lookup[slug]
        if args.dry_run:
            continue
        report_path = report_generator.write_client_report(client, fixes)
        rel = str(report_path.relative_to(ROOT))
        report_entry = {
            "client_slug": slug,
            "client_name": client["client_name"],
            "path":        rel,
            "fixes_count": len(fixes),
            "published_url": "",
        }

        # ── PHASE 6: publish ──
        if not args.no_publish and client.get("deliverables_repo") and publish_briefs.is_enabled():
            push = publish_briefs.publish_one(
                client=client,
                local_path=rel,
                markdown=report_path.read_text(encoding="utf-8"),
                prompt={"template_id": "action-plan", "market": "all"},
            )
            if push.get("ok"):
                report_entry["published_url"] = push.get("remote_url", "")
            else:
                report_entry["publish_error"] = push.get("error", "unknown")
        snapshot["reports"].append(report_entry)
        # Decorate the prompt record with the report path so dashboard can link
        for p in snapshot["prompts"]:
            if p["client_slug"] == slug and p["tier"] in {"Alert", "Launch"}:
                p["brief_path"] = rel

    # ── Persist + dashboard ──
    snapshot["escalations"].sort(key=lambda e: -citation_diff.TIER_ORDER.get(e["tier"], 0))

    if not args.dry_run:
        history_path = HISTORY_DIR / f"{today}.json"
        write_json_file(history_path, snapshot)
        write_json_file(SEEN_PATH, seen_store)
        write_json_file(ACTIVE_TIERS_PATH, active_tiers)
        dash_path = write_dashboard(snapshot)
        _write_summary_md(snapshot)
        print(f"Wrote {history_path.relative_to(ROOT)}", file=sys.stderr)
        print(f"Wrote {dash_path.relative_to(ROOT)}", file=sys.stderr)
        print(f"Wrote {SUMMARY_PATH.relative_to(ROOT)}", file=sys.stderr)

    print(json.dumps({
        "date":             snapshot["date"],
        "tier_counts":      snapshot["tier_counts"],
        "prompts_scanned":  snapshot["prompts_scanned"],
        "engines_used":     snapshot["engines_used"],
        "escalations":      len(snapshot["escalations"]),
        "fixes_generated":  snapshot["fixes_generated"],
        "reports":          len(snapshot["reports"]),
        "dry_run":          args.dry_run,
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
