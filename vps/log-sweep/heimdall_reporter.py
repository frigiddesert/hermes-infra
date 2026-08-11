"""
VENDORED COPY — synced by hand from heimdall repo's clients/python/heimdall.py (issue #25).
Do not diverge; if you need a change, make it upstream and re-copy so other Python reporters
stay in lockstep. Last synced 2026-08-11.

Heimdall reporter for Python services — the language-agnostic counterpart to @heimdall/bifrost.

The Heimdall wire format is just JSON, so a Python app joins the platform with no SDK: this module
POSTs HeimdallEvent JSON to the hub's /ingest (trusted door, service key). Like Bifröst, every public
call is wrapped so it can NEVER raise into the host app — telemetry must not crash the thing it watches.

Stdlib only (urllib) — drop it into any Python 3.9+ service. Delivery is batched on a background
daemon thread with a bounded queue (oldest-dropped) and retry/backoff.

Usage:
    import heimdall
    heimdall.init(app_id="contour-growth-engine", env="production",
                  commit=os.environ.get("GIT_SHA", "dev"),
                  hub_url="https://heimdall-hub.workers.dev",
                  api_key=os.environ["HEIMDALL_SERVICE_KEY"])

    with heimdall.guard("fetch_upstream"):          # reports boundary_failure on exception
        data = fetch_upstream()

    heimdall.security(type="access_denied",
                      title="non-allowlisted Telegram user messaged the bot",
                      technical=f"user_id={uid}")

    heimdall.heartbeat()   # liveness for unreliable/desktop services (see the hub's staleness check)
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

# event types — must match contracts/src/event.ts exactly
_RELIABILITY = {"exception", "boundary_failure", "schema_violation", "data_integrity", "degraded", "synthetic_fail"}
_SECURITY = {"auth_anomaly", "access_denied", "integrity_violation", "secret_exposure", "dependency_vuln", "anomalous_traffic", "config_drift"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Client:
    def __init__(self, app_id: str, env: str, commit: str, hub_url: str, api_key: str,
                 batch_size: int = 10, flush_interval_s: float = 5.0, max_buffered: int = 1000):
        self.app_id = app_id
        self.env = env
        self.commit = commit
        self.hub_url = hub_url.rstrip("/")
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=max_buffered)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._inflight = 0  # batches pulled from the queue but not yet delivered (Codex review #11)
        self._t = threading.Thread(target=self._run, name="heimdall-sender", daemon=True)
        self._t.start()

    # --- public surface (never raises) ---
    def report(self, type: str, title: str, severity: Optional[str] = None,
               user_message: Optional[str] = None, technical: Optional[str] = None,
               ctx: Optional[dict] = None) -> None:
        try:
            if severity is None:
                severity = "critical" if type == "exception" else "warn" if type == "degraded" else "error"
            event = {
                "appId": self.app_id, "env": self.env, "commit": self.commit, "ts": _now_iso(),
                "type": type, "severity": severity, "title": title,
            }
            if user_message is not None:
                event["userMessage"] = user_message
            if technical is not None:
                event["technical"] = technical
            if ctx:
                event["correlationId"] = ctx.get("correlationId")
                event["context"] = {k: v for k, v in ctx.items() if k != "correlationId"}
            self._enqueue(event)
        except Exception:
            pass  # telemetry must never break the host

    def security(self, type: str, title: str, severity: str = "error",
                 technical: Optional[str] = None, ctx: Optional[dict] = None) -> None:
        try:
            is_security = type in _SECURITY  # membership check can raise on an unhashable arg
        except Exception:
            is_security = False
        if not is_security:
            type = "config_drift"  # be safe rather than drop a security signal
        self.report(type=type, title=title, severity=severity, technical=technical, ctx=ctx)

    def degraded(self, feature: str, reason: str, ctx: Optional[dict] = None) -> None:
        self.report(type="degraded", title=f"degraded: {feature}", severity="warn",
                    user_message=reason, ctx=ctx)

    def heartbeat(self) -> None:
        """Liveness ping for unreliable/desktop services — POSTs to /heartbeat (no incident).
        The hub raises synthetic_fail if heartbeats go stale."""
        try:
            self._post("/heartbeat", {"app_id": self.app_id, "ts": _now_iso()})
        except Exception:
            pass

    def flush(self, timeout_s: float = 5.0) -> None:
        # wait for BOTH an empty queue AND zero in-flight deliveries, so flush() doesn't return while
        # a batch is still being sent or retried (Codex review #11)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self._lock:
                inflight = self._inflight
            if self._q.empty() and inflight == 0:
                return
            time.sleep(0.05)

    # --- internals ---
    def _enqueue(self, event: dict) -> None:
        try:
            self._q.put_nowait(event)
        except queue.Full:
            try:
                self._q.get_nowait()  # drop oldest
                self._q.put_nowait(event)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            batch: list[dict] = []
            try:
                batch.append(self._q.get(timeout=self.flush_interval_s))
            except queue.Empty:
                continue
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            with self._lock:
                self._inflight += 1
            try:
                self._deliver(batch)
            finally:
                with self._lock:
                    self._inflight -= 1

    def _deliver(self, batch: list[dict], attempt: int = 0) -> None:
        try:
            self._post("/ingest", {"events": batch})
        except Exception:
            if attempt < 5:
                time.sleep(min(self.flush_interval_s * (2 ** attempt), 60))
                self._deliver(batch, attempt + 1)
            # else: give up on this batch rather than grow unbounded

    def _post(self, path: str, body: dict) -> None:
        # Explicit User-Agent (patched locally, issue #25): the hub's Cloudflare zone blocks the
        # default "Python-urllib/x.y" UA (error code 1010, a 403 before the Worker even runs) — bit
        # openclaw-vps during first deploy. Upstream clients/python/heimdall.py has the same gap.
        req = urllib.request.Request(
            f"{self.hub_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
                "user-agent": "heimdall-python-reporter/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"hub {resp.status}")


_default: Optional[_Client] = None


def init(app_id: str, env: str, commit: str, hub_url: str, api_key: str, **kwargs: Any) -> None:
    global _default
    try:
        _default = _Client(app_id, env, commit, hub_url, api_key, **kwargs)
    except Exception:
        _default = None  # init must never break startup


# Module wrappers are defensively guarded too (Codex review #11): even malformed arguments or a
# broken client can never raise into the host application.
def report(type: str, title: str, **kwargs: Any) -> None:
    try:
        if _default:
            _default.report(type, title, **kwargs)
    except Exception:
        pass


def security(type: str, title: str, **kwargs: Any) -> None:
    try:
        if _default:
            _default.security(type, title, **kwargs)
    except Exception:
        pass


def degraded(feature: str, reason: str, ctx: Optional[dict] = None) -> None:
    try:
        if _default:
            _default.degraded(feature, reason, ctx)
    except Exception:
        pass


def heartbeat() -> None:
    try:
        if _default:
            _default.heartbeat()
    except Exception:
        pass


def flush(timeout_s: float = 5.0) -> None:
    try:
        if _default:
            _default.flush(timeout_s)
    except Exception:
        pass


@contextmanager
def guard(op: str, correlation_id: Optional[str] = None, reraise: bool = False):
    """Wrap an external boundary. On exception, reports a boundary_failure and (by default)
    SUPPRESSES it so one feature degrades instead of crashing the app. Set reraise=True to
    report-and-propagate."""
    try:
        yield
    except Exception as e:
        if _default:
            _default.report(type="boundary_failure", severity="error",
                            title=f"boundary failed: {op}", technical=repr(e),
                            ctx={"op": op, "correlationId": correlation_id})
        if reraise:
            raise
