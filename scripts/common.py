#!/usr/bin/env python3
"""Shared helpers: env loading, JSON IO, SSL, slugging, domain normalisation."""

from __future__ import annotations

import json
import os
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = ROOT / ".env"


def load_env_file(path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
        # Set if absent OR if an existing value is empty (Windows often pre-creates blank vars).
        # Non-empty existing env vars (e.g. GitHub Actions secrets) always win.
        if not os.environ.get(key):
            os.environ[key] = value
    return values


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_file(path: Path, payload: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_ssl_context() -> ssl.SSLContext:
    """Use the OS cert store by default (good on Windows + macOS). On Linux/CI,
    if the system store is empty, fall back to certifi or known cert bundles."""
    # 1) System default (Windows: SChannel, macOS: SecureTransport, modern Linux: openssl)
    ctx = ssl.create_default_context()
    # On Windows the default ctx uses certifi if installed, which can be stale.
    # If the cert chain validates against the system store, prefer that. We test by
    # checking get_ca_certs() — empty list means we need an explicit cafile.
    try:
        if ctx.get_ca_certs():
            return ctx
    except Exception:
        pass

    # 2) Try certifi
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

    # 3) Linux fallback bundles
    for fallback in (
        Path("/etc/ssl/certs/ca-certificates.crt"),
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
        Path("/etc/ssl/cert.pem"),
    ):
        if fallback.exists():
            return ssl.create_default_context(cafile=str(fallback))

    return ctx


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_domain(value: str) -> str:
    """Reduce any URL or domain to bare registrable host, lowercase, no www."""
    if not value:
        return ""
    value = value.strip().lower()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    host = urlparse(value).netloc or value
    host = host.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
