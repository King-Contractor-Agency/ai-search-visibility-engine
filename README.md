# AI Search Visibility Engine

Agent-orchestrated LLM-citation engine for roofing clients. Click once → many
agents → one ready-to-implement action plan per client with the **actual fixes
written out**, not just descriptions of fixes.

## Pipeline (per click / per scheduled run)

1. **Scan** — fire buyer-intent prompts at ChatGPT (web_search) + Gemini (grounded search). 96 prompts × 2 engines by default.
2. **Tier** — every prompt is classed Clear / Watch / Alert / Launch (never-downgrade hold).
3. **Crawl** — for each client with losing prompts, fetch their sitemap so the agents know what URLs already exist.
4. **Diagnose** — parallel **Claude diagnostic agents** read what each AI engine actually said, who got cited, and the client's pages. Each emits a structured fix-spec: type (`blog-post` / `new-page` / `page-update` / `schema` / `gbp-update` / `internal-link`), target URL, required sections, must-include entities, urgency.
5. **Implement** — parallel **Claude implementation agents** turn each spec into the **complete deliverable**:
   - blog-post → full markdown with YAML front matter, body, FAQs, schema, internal links
   - new-page → complete service-area page
   - page-update → before/after diffs of title, meta, H1, paragraphs
   - schema → full JSON-LD ready to paste into `<head>`
   - gbp-update → exact Google Business Profile post + photo brief
   - internal-link → source URL, target URL, anchor text, the sentence to add
6. **Report** — one consolidated markdown action plan per client: `output/reports/<client>/YYYY-MM-DD__action-plan.md`. Executive summary + every fix appended verbatim.
7. **Publish** *(optional)* — auto-commits each client's action plan to their own deliverables repo via the GitHub Contents API.
8. **Dashboard** — `docs/index.html` shows tier counts, escalations, per-client action plans with direct links.

## Setup runbook

### Step 1 — Rotate your 3 keys
- OpenAI: https://platform.openai.com/api-keys
- Gemini: https://aistudio.google.com/app/apikey
- Anthropic: https://console.anthropic.com/settings/keys

### Step 2 — Push to a new private GitHub repo
```powershell
cd "C:\Users\mhefe\OneDrive\Desktop\ai-search-visibility-engine"
git init
git add .
git commit -m "Initial commit"
```
New repo on GitHub (private), then run the `git remote add` + `git push` commands GitHub shows.

### Step 3 — Fix the dashboard's GitHub link
Edit `docs/index.html`, find `__OWNER__/__REPO__` (2 places), replace with `<your-user>/ai-search-visibility-engine`. Commit + push.

### Step 4 — Add 3 secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `ANTHROPIC_API_KEY`

(Optional `DELIVERABLES_PAT` later — see Step 7.)

### Step 5 — Enable GitHub Pages
**Settings → Pages → Source: main / /docs → Save**. Dashboard URL appears at `https://<you>.github.io/ai-search-visibility-engine/`.

### Step 6 — First run
Open the dashboard. Click **Run Scan**. Modal asks for a fine-grained GitHub PAT (Actions: Read+Write on this repo). Paste it, click Run.

First scan takes ~8-15 min (scan + 30-50 concurrent agent jobs). When done:
- Dashboard auto-refreshes
- `output/reports/<client>/<date>__action-plan.md` for each affected client
- `docs/latest_summary.md` summary you can paste into a Claude chat

### Step 7 — *(optional)* Auto-publish to client repos
Fill the 3 columns per client in `data/client_targets.csv`:
```
deliverables_repo,deliverables_branch,deliverables_path
kingcontractor/horvath-seo,main,content/ai-action-plans/
```
Create a fine-grained PAT with **Contents: Read+Write** on all those target repos. Add as secret `DELIVERABLES_PAT`. Next scan: each client's action plan auto-commits to their repo at `<deliverables_path>/YYYY-MM-DD__action-plan.md`.

## Cost ceiling per scan (8 clients, PROMPT_LIMIT=12)
| Phase | Calls | ~Cost |
|---|---|---|
| Scan engines | 192 | $0.40 |
| Diagnostic agents (Sonnet) | ~30-40 | $1.50 |
| Implementation agents (Opus) | ~30-40 | $6 |
| **Total per scan** | | **~$8** |

Monthly cron + 2-3 manual triggers = roughly **$25-40/mo** in API spend. Sellable at $10K/mo on margin alone.

## File layout
```
.github/workflows/
  ai-visibility-scan.yml      # monthly cron + manual dispatch
scripts/
  common.py                   # env, IO, SSL
  prompt_builder.py           # template × market × company_name expansion
  engine_openai.py            # ChatGPT with web_search
  engine_gemini.py            # Gemini grounded
  citation_diff.py            # tier logic
  page_crawler.py             # client sitemap + page meta
  agent_diagnostic.py         # Claude → fix-spec JSON
  agent_implementation.py     # Claude → ready-to-ship deliverable
  report_generator.py         # consolidated action plan per client
  publish_briefs.py           # push reports to client repos
  build_dashboard.py          # → docs/data.json
  run_scan.py                 # orchestrator (concurrent)
data/
  client_targets.csv          # 8 clients, real Semrush data baseline
  prompt_templates.csv        # 18 templates derived from real Semrush prompts
output/
  reports/<client>/<date>__action-plan.md
docs/
  index.html                  # KCA-styled mobile dashboard with Run Scan
  data.json
  latest_summary.md           # paste-into-Claude summary
```

## What about Google Search Console?
v2 add — needs a service account JSON in a secret. For v1 we crawl client
sites directly (no auth) which gives us 80% of the same context (their
existing URLs, page titles, services). Open an issue if you want GSC wired up.
