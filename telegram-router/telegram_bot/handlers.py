"""
telegram_bot/handlers.py — All Telegram command and message handlers.
"""

import os
import subprocess
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .project_router import ProjectRouter

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VPS_SSH_HOST = os.getenv("VPS_SSH_HOST", "openclaw")

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
    """Route incoming messages. Handles /models conversation state, then routes to Claude."""
    msg = update.message
    if not msg or not msg.text:
        return

    # Check if we're mid-conversation in the /models flow (works in DMs or forums)
    if await handle_model_search_reply(update, context):
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


async def _fetch_openrouter_models(search: str, free_only: bool = False, cheapest: bool = False) -> list[dict]:
    """Fetch models from OpenRouter API, filter, and sort."""
    async with httpx.AsyncClient(timeout=15) as client:
        headers = {}
        if OPENROUTER_API_KEY:
            headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
        resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    models = data.get("data", [])

    # Filter
    if free_only:
        models = [m for m in models if m.get("id", "").endswith(":free")]
    elif search and search.lower() not in ("cheap", "cheapest", "free"):
        q = search.lower()
        models = [m for m in models if q in m.get("id", "").lower() or q in m.get("name", "").lower()]

    # Sort
    def prompt_price(m):
        try:
            return float(m.get("pricing", {}).get("prompt", 999))
        except (TypeError, ValueError):
            return 999.0

    if cheapest or free_only:
        models.sort(key=prompt_price)
    else:
        # Sort by relevance (exact id match first), then alphabetically
        q = search.lower() if search else ""
        models.sort(key=lambda m: (0 if q in m.get("id", "").lower() else 1, m.get("id", "")))

    return models[:10]


def _format_model_list(models: list[dict]) -> str:
    lines = []
    for i, m in enumerate(models, 1):
        mid = m.get("id", "unknown")
        name = m.get("name", mid)
        try:
            prompt_price = float(m.get("pricing", {}).get("prompt", 0))
            if prompt_price == 0:
                price_str = "FREE"
            elif prompt_price < 0.000001:
                price_str = f"${prompt_price * 1_000_000:.4f}/1M"
            else:
                price_str = f"${prompt_price * 1_000_000:.2f}/1M"
        except (TypeError, ValueError):
            price_str = "?"
        lines.append(f"{i}. `{mid}` — {price_str}")
    return "\n".join(lines) if lines else "No models found."


def _set_vps_model(model_id: str) -> tuple[bool, str]:
    """SSH to VPS and update the active model. Returns (success, message)."""
    result = subprocess.run(
        ["ssh", VPS_SSH_HOST, f"/root/scripts/set-model.sh '{model_id}'"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip() or result.stdout.strip()


async def handle_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/models — Interactive OpenRouter model browser and switcher."""
    msg = update.message
    if not msg:
        return

    # Clear any previous model-search state
    context.user_data.pop("models_results", None)
    context.user_data["models_waiting"] = "search"

    await msg.reply_text(
        "🔍 *Model Browser*\n\n"
        "Send a search term (e.g. `claude`, `kimi`, `qwen`)\n"
        "or type `cheap` for the 10 least expensive\n"
        "or type `free` for free-tier only",
        parse_mode="Markdown",
    )


async def handle_models_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/models-free — List all free OpenRouter models and let user pick one."""
    msg = update.message
    if not msg:
        return

    await msg.reply_text("🆓 Fetching free models from OpenRouter...")

    try:
        models = await _fetch_openrouter_models("", free_only=True)
    except Exception as e:
        await msg.reply_text(f"❌ Error fetching models: {e}")
        return

    if not models:
        await msg.reply_text("No free models found.")
        return

    context.user_data["models_results"] = [m.get("id") for m in models]
    context.user_data["models_waiting"] = "pick"

    await msg.reply_text(
        f"*Free models on OpenRouter:*\n\n{_format_model_list(models)}\n\n"
        f"Reply with a number (1–{len(models)}) to switch, or /cancel",
        parse_mode="Markdown",
    )


async def handle_model_search_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle multi-step /models conversation. Returns True if message was consumed."""
    msg = update.message
    if not msg or not msg.text:
        return False

    state = context.user_data.get("models_waiting")
    if not state:
        return False

    text = msg.text.strip()

    if state == "search":
        # User sent a search term
        context.user_data.pop("models_waiting", None)
        await msg.reply_text(f"🔍 Searching for `{text}`...", parse_mode="Markdown")

        free_only = text.lower() == "free"
        cheapest = text.lower() in ("cheap", "cheapest")

        try:
            models = await _fetch_openrouter_models(text, free_only=free_only, cheapest=cheapest)
        except Exception as e:
            await msg.reply_text(f"❌ Error fetching models: {e}")
            return True

        if not models:
            await msg.reply_text("No models found. Try a different search term.")
            return True

        context.user_data["models_results"] = [m.get("id") for m in models]
        context.user_data["models_waiting"] = "pick"

        label = "cheapest" if cheapest else ("free" if free_only else f'"{text}"')
        await msg.reply_text(
            f"*Models matching {label}:*\n\n{_format_model_list(models)}\n\n"
            f"Reply with a number (1–{len(models)}) to switch, or /cancel",
            parse_mode="Markdown",
        )
        return True

    if state == "pick":
        # User sent a number to pick a model
        if text.lower() in ("/cancel", "cancel"):
            context.user_data.pop("models_waiting", None)
            context.user_data.pop("models_results", None)
            await msg.reply_text("Cancelled.")
            return True

        results = context.user_data.get("models_results", [])
        try:
            idx = int(text) - 1
            if not (0 <= idx < len(results)):
                raise ValueError()
        except ValueError:
            await msg.reply_text(f"Please send a number between 1 and {len(results)}, or /cancel")
            return True

        model_id = results[idx]
        context.user_data.pop("models_waiting", None)
        context.user_data.pop("models_results", None)

        await msg.reply_text(f"⚡ Switching to `{model_id}`...", parse_mode="Markdown")
        success, output = _set_vps_model(model_id)
        if success:
            await msg.reply_text(f"✅ Active model: `{model_id}`", parse_mode="Markdown")
        else:
            await msg.reply_text(f"❌ Failed:\n```\n{output[:500]}\n```", parse_mode="Markdown")
        return True

    return False


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
