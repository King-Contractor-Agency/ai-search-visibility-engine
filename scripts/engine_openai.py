#!/usr/bin/env python3
"""OpenAI Responses API with web_search tool — returns annotations with url_citations."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from common import build_ssl_context, normalize_domain


API_URL = "https://api.openai.com/v1/responses"
MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ENGINE  = "chatgpt"


def is_enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _build_payload(prompt: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "input": [
            {"role": "system", "content": "You are a local-service-business research assistant. Always recommend specific named companies and cite the sources."},
            {"role": "user",   "content": prompt},
        ],
        "tools":   [{"type": "web_search"}],
        "max_output_tokens": 700,
    }


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=60, context=build_ssl_context()) as response:
        return json.load(response)


def _extract(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    pos = 0

    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") not in {"output_text", "text"}:
                continue
            text_parts.append(content.get("text", "") or "")
            for ann in content.get("annotations") or []:
                if ann.get("type") != "url_citation":
                    continue
                url = ann.get("url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                pos += 1
                citations.append({
                    "position": pos,
                    "url":      url,
                    "domain":   normalize_domain(url),
                    "title":    ann.get("title", "") or "",
                })
    return "\n\n".join(text_parts).strip(), citations


def query(prompt_text: str) -> dict[str, Any]:
    try:
        data = _post(_build_payload(prompt_text))
    except error.HTTPError as exc:
        return _err(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}")
    except error.URLError as exc:
        return _err(f"URL error: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _err(f"unexpected: {exc}")

    text, citations = _extract(data)
    return {
        "engine":    ENGINE,
        "model":     MODEL,
        "ok":        True,
        "error":     "",
        "answer":    text,
        "citations": citations,
    }


def _err(msg: str) -> dict[str, Any]:
    return {"engine": ENGINE, "model": MODEL, "ok": False, "error": msg, "answer": "", "citations": []}
