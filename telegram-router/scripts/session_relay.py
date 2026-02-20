"""
scripts/session_relay.py — Watch Claude .jsonl session files and relay
assistant text to the matching Telegram forum topic.

Runs as a standalone process; polls every 2 seconds.

Usage:
    python -m scripts.session_relay
"""

import glob
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.topic_manager import TopicManager

PROJECTS_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))
POLL_INTERVAL = float(os.getenv("RELAY_POLL_INTERVAL", "2"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FORUM_ID = int(os.environ["TELEGRAM_FORUM_ID"])
DB_URL = os.environ["DATABASE_URL"]


# ── Project name extraction ───────────────────────────────────────────────────

def extract_project_name(dir_path: str) -> str | None:
    """
    Convert a .claude/projects directory name to a project name.

    Examples:
        -home-eric-code-nexus            → nexus
        -home-eric-code-my-project       → my-project
        -home-eric-code-Arctic-API-...   → Arctic-API-...
    """
    name = os.path.basename(dir_path)
    # Find "code" segment and take everything after it
    parts = name.split("-")
    try:
        code_idx = next(i for i, p in enumerate(parts) if p == "code")
        suffix = "-".join(parts[code_idx + 1 :])
        return suffix if suffix else None
    except StopIteration:
        return name or None


# ── Relay ─────────────────────────────────────────────────────────────────────

class SessionRelay:
    def __init__(self, tm: TopicManager):
        self.tm = tm
        # file_path → byte offset we've read up to
        self._positions: dict[str, int] = {}

    def run(self) -> None:
        print(f"[session_relay] watching {PROJECTS_DIR}  (poll every {POLL_INTERVAL}s)")
        while True:
            try:
                self._scan()
            except Exception as exc:
                print(f"[session_relay] error: {exc}", flush=True)
            time.sleep(POLL_INTERVAL)

    def _scan(self) -> None:
        pattern = str(PROJECTS_DIR / "*" / "*.jsonl")
        for jsonl_path in glob.glob(pattern):
            self._relay_file(jsonl_path)

    def _relay_file(self, jsonl_path: str) -> None:
        project_dir = os.path.dirname(jsonl_path)
        project_name = extract_project_name(project_dir)
        if not project_name:
            return

        last_pos = self._positions.get(jsonl_path, 0)
        try:
            stat = os.stat(jsonl_path)
        except FileNotFoundError:
            return

        if stat.st_size <= last_pos:
            return

        try:
            with open(jsonl_path, "rb") as f:
                f.seek(last_pos)
                new_data = f.read()
                self._positions[jsonl_path] = f.tell()
        except (FileNotFoundError, PermissionError):
            return

        for raw_line in new_data.split(b"\n"):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            self._handle_event(project_name, data)

    def _handle_event(self, project_name: str, data: dict) -> None:
        msg_type = data.get("type")

        if msg_type == "assistant":
            content = data.get("message", {}).get("content", [])
            text = _extract_text(content)
            if text:
                try:
                    topic_id = self.tm.get_or_create(project_name)
                    self.tm.send(topic_id, text)
                    print(f"[session_relay] {project_name} → topic {topic_id}: {text[:60]}…")
                except Exception as exc:
                    print(f"[session_relay] send error ({project_name}): {exc}")


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return ""


if __name__ == "__main__":
    if not FORUM_ID:
        print("ERROR: TELEGRAM_FORUM_ID not set in .env — cannot relay.", file=sys.stderr)
        sys.exit(1)

    tm = TopicManager(
        bot_token=BOT_TOKEN,
        forum_id=FORUM_ID,
        database_url=DB_URL,
    )
    relay = SessionRelay(tm)
    relay.run()
