"""Loads scanner-config.yaml.

No PyYAML (stdlib-only constraint) — this is a tiny hand-rolled parser for exactly the shape
scanner-config.yaml uses: a top-level `repos:` key holding a list of flat dicts. It is NOT a
general YAML parser; don't add nested structures to the config without extending this.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "scanner-config.yaml"


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw in ("null", "~", ""):
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')) and len(raw) >= 2:
        return raw[1:-1]
    return raw


def load_repos(path: Path | None = None) -> list[dict[str, Any]]:
    text = (path or CONFIG_PATH).read_text()
    repos: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_repos = False
    for raw_line in text.splitlines():
        line = raw_line.split(" #", 1)[0].rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("repos:"):
            in_repos = True
            continue
        if not in_repos:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                repos.append(current)
            current = {}
            stripped = stripped[2:]
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                current[k.strip()] = _parse_scalar(v)
            continue
        if current is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            current[k.strip()] = _parse_scalar(v)
    if current is not None:
        repos.append(current)
    return repos
