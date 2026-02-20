"""
telegram_bot/handlers.py — All Telegram command and message handlers.
"""

import os
import subprocess
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .project_router import ProjectRouter

INBOX_DIR = Path(os.getenv("INBOX_DIR", Path.home() / ".claude" / "inbox"))
FORUM_ID = int(os.getenv("TELEGRAM_FORUM_ID", "0"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_tmux_session(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def _last_assistant_message(project_name: str) -> str | None:
    """Read the most recent assistant text from the project's .jsonl files."""
    import glob, json

    projects_dir = Path(os.getenv("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))
    # Find matching project directory
    pattern = str(projects_dir / f"*{project_name}*" / "*.jsonl")
    files = sorted(glob.glob(pattern))
    if not files:
        return None

    latest = files[-1]
    last_text = None
    try:
        with open(latest) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "assistant":
                    content = data.get("message", {}).get("content", [])
                    text = _extract_text(content)
                    if text:
                        last_text = text
    except (FileNotFoundError, PermissionError):
        pass

    return last_text


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return ""


# ── Handlers ─────────────────────────────────────────────────────────────────

async def handle_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chatid — discover the forum group ID and current topic thread ID."""
    msg = update.message
    if not msg:
        return
    lines = [f"**Chat ID:** `{msg.chat_id}`"]
    if msg.message_thread_id:
        lines.append(f"**Thread ID:** `{msg.message_thread_id}`")
    lines.append("\nSet `TELEGRAM_FORUM_ID` in your `.env` to the Chat ID above.")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topics — list all known project→topic mappings."""
    msg = update.message
    if not msg:
        return
    router: ProjectRouter = context.bot_data["router"]

    # Refresh from DB
    await router._warm_cache()

    if not router._reverse_cache:
        await msg.reply_text("No project topics mapped yet.")
        return

    lines = ["**Project → Topic ID**"]
    for name, tid in sorted(router._reverse_cache.items()):
        lines.append(f"  `{name}` → {tid}")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop or /interrupt — send Ctrl+C to the project's tmux session."""
    msg = update.message
    if not msg or not msg.message_thread_id:
        return

    router: ProjectRouter = context.bot_data["router"]
    project_name = await router.get_project_from_thread(msg.message_thread_id)

    if not project_name:
        await msg.reply_text("⚠️ Unknown topic — no project mapped to this thread.")
        return

    if not _has_tmux_session(project_name):
        await msg.reply_text(f"ℹ️ No active tmux session for `{project_name}` on this machine.", parse_mode="Markdown")
        return

    subprocess.run(["tmux", "send-keys", "-t", project_name, "C-c", ""])
    last = _last_assistant_message(project_name) or "(no recent output)"
    await msg.reply_text(
        f"⏹ **Interrupted** `{project_name}`\n\nLast output:\n```\n{last[:800]}\n```\n\nWhat would you like to do next?",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming forum topic messages into the matching Claude tmux session."""
    msg = update.message
    if not msg or not msg.text:
        return

    # Not in a forum topic — print IDs to help with setup
    if not msg.message_thread_id:
        if msg.chat.id == FORUM_ID or FORUM_ID == 0:
            await msg.reply_text(
                f"ℹ️ Send this in a forum topic to route to Claude.\n"
                f"Chat ID: `{msg.chat_id}`",
                parse_mode="Markdown",
            )
        return

    router: ProjectRouter = context.bot_data["router"]
    project_name = await router.get_project_from_thread(msg.message_thread_id)

    if not project_name:
        # Unknown topic — acknowledge but don't route
        return

    if not _has_tmux_session(project_name):
        # Not our machine — silently ignore
        return

    # Write message to inbox for inbox_watcher to pick up
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    inbox_file = INBOX_DIR / f"{project_name}.txt"
    inbox_file.write_text(msg.text)
    print(f"[handlers] → {project_name}: {msg.text[:80]}")


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for approve/deny inline buttons from permission relay."""
    query = update.callback_query
    await query.answer()

    data = query.data  # "approve:<session>:<tool>" or "deny:<session>:<tool>"
    action, session, tool = data.split(":", 2)
    kb = InlineKeyboardMarkup([])

    if action == "approve":
        subprocess.run(["tmux", "send-keys", "-t", session, "y", "Enter"])
        await query.edit_message_text(f"✅ Approved `{tool}` for `{session}`", parse_mode="Markdown", reply_markup=kb)
    elif action == "deny":
        subprocess.run(["tmux", "send-keys", "-t", session, "n", "Enter"])
        await query.edit_message_text(f"❌ Denied `{tool}` for `{session}`", parse_mode="Markdown", reply_markup=kb)
