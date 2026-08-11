"""Module 3: Cloudflare account posture (Eric's new request, layered onto issue #24).

TOKEN-GATED: reads CLOUDFLARE_API_TOKEN (+ CLOUDFLARE_ACCOUNT_ID) from security-scanner/.env. If
absent, logs "cf-posture: skipped (no token)" and returns cleanly — this is the expected state
until Eric mints a token (see README for required scopes). Nothing else in the scan is affected.

When a token IS present:
  - list workers + pages projects, diff against posture-baseline.json (today's ground truth) AND
    the heimdall apps registry (by name) → a worker/pages project that is in NEITHER is an
    unregistered deployment → config_drift warn incident.
  - a worker crossing 90 days unmodified accumulates silently into the orphan ledger (NOT an
    incident) — queryable later, never paged.
  - list Access apps + their policies; a NEW policy whose decision is "bypass" and whose include
    list contains "everyone" is a real act-now signal → config_drift CRITICAL incident.
  - list API tokens (/user/tokens): a token unused >60 days accumulates into the orphan ledger; a
    NEWLY created token (not seen in prior state) pages (config_drift warn).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from common import log, make_event, resolve_cf_account_id, resolve_cf_token

API_BASE = "https://api.cloudflare.com/client/v4"
STALE_WORKER_DAYS = 90
STALE_TOKEN_DAYS = 60
HEIMDALL_PLATFORM_APP_ID = "heimdall-platform"


def _cf_get(path: str, token: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log(f"cf-posture: GET {path} -> http {e.code}")
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"cf-posture: GET {path} failed: {e}")
        return None


def fetch_workers(account_id: str, token: str) -> list[dict[str, Any]]:
    data = _cf_get(f"/accounts/{account_id}/workers/scripts", token)
    if not data or not data.get("success"):
        return []
    return [{"name": w.get("id") or w.get("name"), "modified_on": w.get("modified_on")}
            for w in data.get("result", [])]


def fetch_pages(account_id: str, token: str) -> list[dict[str, Any]]:
    data = _cf_get(f"/accounts/{account_id}/pages/projects", token)
    if not data or not data.get("success"):
        return []
    out = []
    for p in data.get("result", []):
        deploy = p.get("latest_deployment") or {}
        out.append({"name": p.get("name"), "modified_on": deploy.get("created_on") or p.get("created_on")})
    return out


def fetch_access_apps(account_id: str, token: str) -> list[dict[str, Any]]:
    data = _cf_get(f"/accounts/{account_id}/access/apps", token)
    if not data or not data.get("success"):
        return []
    return data.get("result", [])


def fetch_access_policies(account_id: str, app_id: str, token: str) -> list[dict[str, Any]]:
    data = _cf_get(f"/accounts/{account_id}/access/apps/{app_id}/policies", token)
    if not data or not data.get("success"):
        return []
    return data.get("result", [])


def fetch_api_tokens(token: str) -> list[dict[str, Any]]:
    data = _cf_get("/user/tokens", token)
    if not data or not data.get("success"):
        return []
    return data.get("result", [])


# --- pure diff logic (unit-tested without network) --------------------------

def _days_since(iso_ts: str | None, now: float) -> float | None:
    if not iso_ts:
        return None
    try:
        ts = time.mktime(time.strptime(iso_ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    return (now - ts) / 86400.0


def known_names_from_baseline(baseline: dict[str, Any]) -> tuple[set[str], set[str]]:
    worker_names = {entry.split("|")[0] for entry in baseline.get("workers", [])}
    page_names = {entry.split("|")[0] for entry in baseline.get("pages", [])}
    return worker_names, page_names


def registered_app_tokens(apps: list[dict[str, Any]]) -> set[str]:
    """Loose name-matching pool: app ids + repo basenames, lowercased. A worker/pages name
    containing (or contained in) one of these is treated as 'registered' — avoids false positives
    from -staging/-dev/-beta suffixes on a genuinely registered app."""
    tokens: set[str] = set()
    for app in apps:
        app_id = (app.get("id") or "").lower()
        if app_id:
            tokens.add(app_id)
        repo = (app.get("repo") or "").rstrip("/").split("/")[-1].lower()
        if repo:
            tokens.add(repo)
    return tokens


def _is_registered(name: str, tokens: set[str]) -> bool:
    n = name.lower()
    return any(t and (t in n or n in t) for t in tokens)


def diff_deployments(kind: str, current: list[dict[str, Any]], baseline_names: set[str],
                      app_tokens: set[str], now: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (new_unregistered, newly_stale) for one deployment kind ('worker' | 'pages')."""
    new_unregistered = []
    newly_stale = []
    for entry in current:
        name = entry.get("name")
        if not name:
            continue
        if name not in baseline_names and not _is_registered(name, app_tokens):
            new_unregistered.append(entry)
        age_days = _days_since(entry.get("modified_on"), now)
        if kind == "worker" and age_days is not None and age_days >= STALE_WORKER_DAYS:
            newly_stale.append({**entry, "age_days": round(age_days)})
    return new_unregistered, newly_stale


def find_bypass_everyone_policies(apps_with_policies: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    hits = []
    for app_name, policies in apps_with_policies.items():
        for pol in policies:
            if (pol.get("decision") or "").lower() != "bypass":
                continue
            includes = pol.get("include") or []
            has_everyone = any("everyone" in json.dumps(i).lower() for i in includes)
            if has_everyone:
                hits.append({"app": app_name, "policy_id": pol.get("id"), "policy_name": pol.get("name")})
    return hits


def diff_api_tokens(tokens: list[dict[str, Any]], seen_token_ids: set[str], now: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (newly_created, stale_unused)."""
    newly_created = [t for t in tokens if t.get("id") and t["id"] not in seen_token_ids]
    stale_unused = []
    for t in tokens:
        age = _days_since(t.get("last_used_on") or t.get("issued_on"), now)
        if age is not None and age >= STALE_TOKEN_DAYS:
            stale_unused.append({**t, "age_days": round(age)})
    return newly_created, stale_unused


# --- orchestration -----------------------------------------------------------

def run(state: dict[str, Any], orphan_ledger: dict[str, Any], baseline: dict[str, Any],
        registered_apps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    token = resolve_cf_token()
    if not token:
        log("cf-posture: skipped (no token)")
        return [], state, orphan_ledger
    account_id = resolve_cf_account_id() or baseline.get("account")
    if not account_id:
        log("cf-posture: skipped (no account id)")
        return [], state, orphan_ledger

    now = time.time()
    app_tokens = registered_app_tokens(registered_apps)
    baseline_workers, baseline_pages = known_names_from_baseline(baseline)

    new_wrappers: list[dict[str, Any]] = []
    seen_drift = set(state.get("drift_seen", []))
    orphan_workers = dict(orphan_ledger.get("stale_workers", {}))
    seen_tokens = set(state.get("seen_token_ids", []))
    orphan_tokens = dict(orphan_ledger.get("unused_tokens", {}))

    workers = fetch_workers(account_id, token)
    new_workers, stale_workers = diff_deployments("worker", workers, baseline_workers, app_tokens, now)
    for w in stale_workers:
        orphan_workers[w["name"]] = {"age_days": w["age_days"], "modified_on": w.get("modified_on")}
    for w in new_workers:
        key = f"drift:worker:{w['name']}"
        if key in seen_drift:
            continue
        seen_drift.add(key)
        event = make_event(
            HEIMDALL_PLATFORM_APP_ID, "config_drift", "warn",
            f"unregistered deployment appeared: worker {w['name']}",
            f"Worker '{w['name']}' is not in the posture baseline and doesn't match any registered heimdall app.",
            context={"op": "cf_posture", "kind": "worker", "name": w["name"]},
        )
        new_wrappers.append({"key": key, "event": event})

    pages = fetch_pages(account_id, token)
    new_pages, _ = diff_deployments("pages", pages, baseline_pages, app_tokens, now)
    for p in new_pages:
        key = f"drift:pages:{p['name']}"
        if key in seen_drift:
            continue
        seen_drift.add(key)
        event = make_event(
            HEIMDALL_PLATFORM_APP_ID, "config_drift", "warn",
            f"unregistered deployment appeared: pages project {p['name']}",
            f"Pages project '{p['name']}' is not in the posture baseline and doesn't match any registered heimdall app.",
            context={"op": "cf_posture", "kind": "pages", "name": p["name"]},
        )
        new_wrappers.append({"key": key, "event": event})

    access_apps = fetch_access_apps(account_id, token)
    apps_with_policies = {}
    for a in access_apps:
        aid, aname = a.get("id"), a.get("name", a.get("id"))
        if not aid:
            continue
        apps_with_policies[aname] = fetch_access_policies(account_id, aid, token)
    for hit in find_bypass_everyone_policies(apps_with_policies):
        key = f"drift:access-bypass:{hit['policy_id']}"
        if key in seen_drift:
            continue
        seen_drift.add(key)
        event = make_event(
            HEIMDALL_PLATFORM_APP_ID, "config_drift", "critical",
            f"Access bypass-everyone policy created: {hit['app']}",
            f"App '{hit['app']}' has a bypass policy ({hit.get('policy_name')}) that includes everyone — authentication is being skipped.",
            context={"op": "cf_posture", "kind": "access_bypass", "app": hit["app"], "policy_id": hit["policy_id"]},
        )
        new_wrappers.append({"key": key, "event": event})

    tokens_list = fetch_api_tokens(token)
    newly_created, stale_unused = diff_api_tokens(tokens_list, seen_tokens, now)
    for t in tokens_list:
        if t.get("id"):
            seen_tokens.add(t["id"])
    for t in stale_unused:
        orphan_tokens[t["id"]] = {"name": t.get("name"), "age_days": t["age_days"]}
    for t in newly_created:
        key = f"drift:new-token:{t['id']}"
        if key in seen_drift:
            continue
        seen_drift.add(key)
        event = make_event(
            HEIMDALL_PLATFORM_APP_ID, "config_drift", "warn",
            f"new Cloudflare API token created: {t.get('name', t['id'])}",
            "A new API token was created on the account — confirm this was intentional.",
            context={"op": "cf_posture", "kind": "new_api_token", "name": t.get("name"), "id": t["id"]},
        )
        new_wrappers.append({"key": key, "event": event})

    state["drift_seen"] = sorted(seen_drift)
    state["seen_token_ids"] = sorted(seen_tokens)
    orphan_ledger["stale_workers"] = orphan_workers
    orphan_ledger["unused_tokens"] = orphan_tokens
    return new_wrappers, state, orphan_ledger
