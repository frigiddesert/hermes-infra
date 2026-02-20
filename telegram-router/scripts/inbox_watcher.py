"""
scripts/inbox_watcher.py — Watch ~/.claude/inbox/ for new .txt files and
inject their contents into the matching Claude tmux session.

Uses inotify for instant detection (Linux/WSL2). Falls back to polling
if inotify is unavailable.

Usage:
    python -m scripts.inbox_watcher
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

INBOX_DIR = Path(os.getenv("INBOX_DIR", Path.home() / ".claude" / "inbox"))
POLL_FALLBACK_INTERVAL = 1.0  # seconds, used if inotify unavailable


# ── Helpers ───────────────────────────────────────────────────────────────────

def has_tmux_session(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def inject_to_tmux(session_name: str, text: str) -> None:
    """Send text to a tmux session followed by Enter."""
    subprocess.run(["tmux", "send-keys", "-t", session_name, text, "Enter"])
    print(f"[inbox_watcher] → tmux:{session_name}: {text[:80]}")


def process_file(filepath: Path) -> None:
    project_name = filepath.stem  # nexus.txt → nexus

    if not has_tmux_session(project_name):
        # Not our machine — leave file in place for other bot instance
        print(f"[inbox_watcher] no tmux session '{project_name}' — skipping")
        return

    try:
        content = filepath.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return

    if not content:
        filepath.unlink(missing_ok=True)
        return

    inject_to_tmux(project_name, content)
    filepath.unlink(missing_ok=True)


# ── Watchers ─────────────────────────────────────────────────────────────────

def run_inotify() -> None:
    import inotify.adapters

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[inbox_watcher] inotify watching {INBOX_DIR}")

    i = inotify.adapters.Inotify()
    i.add_watch(str(INBOX_DIR))

    for event in i.event_gen(yield_nones=False):
        (_, type_names, path, filename) = event
        if "IN_CLOSE_WRITE" in type_names or "IN_MOVED_TO" in type_names:
            if filename.endswith(".txt"):
                process_file(Path(path) / filename)


def run_polling() -> None:
    """Fallback polling watcher (macOS / systems without inotify)."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[inbox_watcher] polling {INBOX_DIR} every {POLL_FALLBACK_INTERVAL}s")
    seen: set[str] = set()

    while True:
        try:
            for f in INBOX_DIR.glob("*.txt"):
                key = f"{f.name}:{f.stat().st_mtime}"
                if key not in seen:
                    seen.add(key)
                    process_file(f)
        except Exception as exc:
            print(f"[inbox_watcher] error: {exc}", flush=True)
        time.sleep(POLL_FALLBACK_INTERVAL)


if __name__ == "__main__":
    try:
        import inotify.adapters  # noqa: F401
        run_inotify()
    except ImportError:
        print("[inbox_watcher] inotify not available, using polling fallback")
        run_polling()
