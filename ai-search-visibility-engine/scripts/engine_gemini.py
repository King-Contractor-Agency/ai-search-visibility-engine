#!/usr/bin/env python3
"""Gemini grounded generation — returns groundingMetadata with citations."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from common import build_ssl_context, normalize_domain


MODEL  = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
ENGINE = "gemini"


def is_enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def _build_payload(prompt: str) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools":    [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 700,
        },
    }


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    url = f"{API_URL}?key={os.environ['GEMINI_API_KEY']}"
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=60, context=build_ssl_context()) as response:
        return json.load(response)


def _extract(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if "text" in part:
                text_parts.append(part["text"] or "")
        grounding = candidate.get("groundingMetadata") or {}
        chunks = grounding.get("groundingChunks") or []
        for idx, chunk in enumerate(chunks, start=1):
            web = chunk.get("web") or {}
            url = web.get("uri") or web.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            citations.append({
                "position": idx,
                "url":      url,
                "domain":   normalize_domain(url),
                "title":    web.get("title", "") or "",
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
