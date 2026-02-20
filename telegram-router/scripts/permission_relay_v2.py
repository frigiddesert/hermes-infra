"""
scripts/permission_relay_v2.py — Watch Claude .jsonl files for tool_use events
and send approve/deny inline buttons to Telegram.

Behavior:
- Detects tool_use without a subsequent tool_result → potential permission request
- Waits PERMISSION_DELAY seconds
- Re-reads the file — if tool_result appeared (auto-accepted), suppresses notification
- If still no tool_result, sends approve/deny buttons to the project's forum topic

Usage:
    python -m scripts.permission_relay_v2
"""

import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.topic_manager import TopicManager
from scripts.session_relay import extract_project_name

PROJECTS_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))
POLL_INTERVAL = float(os.getenv("RELAY_POLL_INTERVAL", "2"))
PERMISSION_DELAY = float(os.getenv("PERMISSION_DELAY", "3.0"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FORUM_ID = int(os.environ["TELEGRAM_FORUM_ID"])
DB_URL = os.environ["DATABASE_URL"]


@dataclass
class PendingPermission:
    project_name: str
    session_name: str  # tmux session
    tool_name: str
    tool_use_id: str
    file_path: str
    byte_offset: int       # position in file where tool_use was found
    detected_at: float     # time.time()
    notified: bool = False


class PermissionRelay:
    def __init__(self, tm: TopicManager):
        self.tm = tm
        self._positions: dict[str, int] = {}
        self._pending: dict[str, PendingPermission] = {}  # tool_use_id → pending

    def run(self) -> None:
        print(f"[permission_relay] watching {PROJECTS_DIR}  (delay={PERMISSION_DELAY}s)")
        while True:
            try:
                self._scan()
                self._check_pending()
            except Exception as exc:
                print(f"[permission_relay] error: {exc}", flush=True)
            time.sleep(POLL_INTERVAL)

    # ── Scanning ──────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        for jsonl_path in glob.glob(str(PROJECTS_DIR / "*" / "*.jsonl")):
            self._scan_file(jsonl_path)

    def _scan_file(self, jsonl_path: str) -> None:
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
            self._handle_event(project_name, jsonl_path, data)

    def _handle_event(self, project_name: str, jsonl_path: str, data: dict) -> None:
        msg_type = data.get("type")

        # A tool_use block appeared — add to pending
        if msg_type == "assistant":
            content = data.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    tool_name = block.get("name", "unknown")
                    if tool_id and tool_id not in self._pending:
                        self._pending[tool_id] = PendingPermission(
                            project_name=project_name,
                            session_name=project_name,
                            tool_name=tool_name,
                            tool_use_id=tool_id,
                            file_path=jsonl_path,
                            byte_offset=self._positions.get(jsonl_path, 0),
                            detected_at=time.time(),
                        )

        # A tool_result appeared — the tool was auto-accepted, remove from pending
        if msg_type == "tool" or (
            msg_type == "user"
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in (data.get("message", {}).get("content") or [])
            )
        ):
            # Find matching pending permission by scanning content
            content = data.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    self._pending.pop(tool_id, None)

    # ── Pending checks ────────────────────────────────────────────────────────

    def _check_pending(self) -> None:
        now = time.time()
        expired = [
            p for p in self._pending.values()
            if not p.notified and now - p.detected_at >= PERMISSION_DELAY
        ]
        for perm in expired:
            # Re-check the file — did a tool_result appear yet?
            if self._tool_result_appeared(perm):
                # Auto-accepted — suppress notification
                del self._pending[perm.tool_use_id]
                continue
            self._send_permission_request(perm)
            perm.notified = True

    def _tool_result_appeared(self, perm: PendingPermission) -> bool:
        """Re-scan the file from perm.byte_offset looking for tool_result for this ID."""
        try:
            with open(perm.file_path, "rb") as f:
                f.seek(perm.byte_offset)
                data = f.read()
        except (FileNotFoundError, PermissionError):
            return False

        for raw_line in data.split(b"\n"):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            content = obj.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == perm.tool_use_id
                ):
                    return True
        return False

    def _send_permission_request(self, perm: PendingPermission) -> None:
        """Send approve/deny buttons to the project's Telegram topic."""
        try:
            topic_id = self.tm.get_or_create(perm.project_name)
        except Exception as exc:
            print(f"[permission_relay] topic lookup failed: {exc}")
            return

        keyboard = {
            "inline_keyboard": [[
                {
                    "text": "✅ Approve",
                    "callback_data": f"approve:{perm.session_name}:{perm.tool_name}",
                },
                {
                    "text": "❌ Deny",
                    "callback_data": f"deny:{perm.session_name}:{perm.tool_name}",
                },
            ]]
        }

        text = (
            f"🔐 **Permission Request**\n"
            f"Project: `{perm.project_name}`\n"
            f"Tool: `{perm.tool_name}`\n"
            f"ID: `{perm.tool_use_id[:12]}…`"
        )

        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": FORUM_ID,
                    "message_thread_id": topic_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard,
                },
                timeout=15,
            )
            print(f"[permission_relay] sent request for {perm.tool_name} → {perm.project_name}")
        except Exception as exc:
            print(f"[permission_relay] send failed: {exc}")


if __name__ == "__main__":
    if not FORUM_ID:
        print("ERROR: TELEGRAM_FORUM_ID not set.", file=sys.stderr)
        sys.exit(1)

    tm = TopicManager(bot_token=BOT_TOKEN, forum_id=FORUM_ID, database_url=DB_URL)
    relay = PermissionRelay(tm)
    relay.run()
