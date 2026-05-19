#!/usr/bin/env python3
"""Push generated AEO briefs into each client's own deliverables repo via the GitHub Contents API.

A client row enables publishing by filling 3 columns in data/client_targets.csv:
  deliverables_repo   = "owner/repo"
  deliverables_branch = "main"          (optional, defaults to main)
  deliverables_path   = "content/blog/ai-briefs/"  (trailing slash optional)

The PAT lives in env var DELIVERABLES_PAT (fine-grained, Contents: Read+Write on each
target repo). If the var is missing or the columns are empty, publishing is a no-op.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib import error, request

from common import build_ssl_context, slugify, today_str


API = "https://api.github.com"


def is_enabled() -> bool:
    return bool(os.environ.get("DELIVERABLES_PAT"))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['DELIVERABLES_PAT']}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":    "kca-ai-visibility-publisher",
    }


def _get(url: str) -> tuple[int, dict[str, Any] | None]:
    req = request.Request(url, headers=_headers(), method="GET")
    try:
        with request.urlopen(req, timeout=30, context=build_ssl_context()) as r:
            return r.status, json.load(r)
    except error.HTTPError as exc:
        if exc.code == 404:
            return 404, None
        raise


def _put(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={**_headers(), "Content-Type": "application/json"}, method="PUT")
    with request.urlopen(req, timeout=45, context=build_ssl_context()) as r:
        return json.load(r)


def publish_one(*, client: dict, local_path: str, markdown: str, prompt: dict) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": False, "error": "DELIVERABLES_PAT not set"}

    repo   = (client.get("deliverables_repo") or "").strip()
    if not repo or "/" not in repo:
        return {"ok": False, "error": "deliverables_repo not configured for client"}

    branch = (client.get("deliverables_branch") or "main").strip() or "main"
    base   = (client.get("deliverables_path") or "").strip().strip("/")
    base   = base + "/" if base else ""

    # Special-case the consolidated action plan
    if prompt.get("template_id") == "action-plan":
        filename = f"{today_str()}__action-plan.md"
    else:
        filename = f"{today_str()}__{slugify(prompt['template_id'])}__{slugify(prompt['market'])}.md"
    remote_path = f"{base}{filename}"
    url = f"{API}/repos/{repo}/contents/{remote_path}"

    # Look up existing SHA if file already exists (so PUT updates rather than fails).
    sha = ""
    try:
        status, existing = _get(f"{url}?ref={branch}")
        if status == 200 and existing and "sha" in existing:
            sha = existing["sha"]
    except error.HTTPError as exc:
        return {"ok": False, "error": f"GET {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}"}
    except error.URLError as exc:
        return {"ok": False, "error": f"GET URL error: {exc}"}

    author_name  = os.environ.get("PUBLISHER_NAME")  or "KCA AI Visibility Bot"
    author_email = os.environ.get("PUBLISHER_EMAIL") or "ai-visibility@kingcontractor.com"
    message = f"AI Visibility brief — {client['client_name']} — {prompt['text'][:60]}"

    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
        "branch":  branch,
        "committer": {"name": author_name, "email": author_email},
        "author":    {"name": author_name, "email": author_email},
    }
    if sha:
        payload["sha"] = sha

    try:
        result = _put(url, payload)
    except error.HTTPError as exc:
        return {"ok": False, "error": f"PUT {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}"}
    except error.URLError as exc:
        return {"ok": False, "error": f"PUT URL error: {exc}"}

    content_meta = result.get("content") or {}
    return {
        "ok":          True,
        "error":       "",
        "remote_repo": repo,
        "remote_path": remote_path,
        "remote_url":  content_meta.get("html_url", ""),
        "commit_sha":  (result.get("commit") or {}).get("sha", ""),
    }
