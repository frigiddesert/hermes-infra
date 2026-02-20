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
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://openclaw-watchdog.eric-c5f.workers.dev")
CF_ADMIN_TOKEN = os.getenv("CF_ADMIN_TOKEN", "")

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


# ── Model browser constants ───────────────────────────────────────────────────

PRICE_LIMIT = 3e-6  # $3 per million tokens — never show above this

PROVIDER_NAMES = {
    "amazon-bedrock": "Amazon Bedrock",
    "anthropic": "Anthropic",
    "azure-openai-responses": "Azure OpenAI",
    "cerebras": "Cerebras",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "featherless": "Featherless",
    "github-copilot": "GitHub Copilot",
    "google": "Google",
    "google-antigravity": "Google (Antigravity)",
    "google-gemini-cli": "Google Gemini CLI",
    "google-vertex": "Google Vertex",
    "groq": "Groq",
    "huggingface": "Hugging Face",
    "inflection": "Inflection",
    "kimi-coding": "Kimi Coding",
    "liquid": "Liquid AI",
    "meta-llama": "Meta Llama",
    "microsoft": "Microsoft",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax CN",
    "mistral": "Mistral AI",
    "mistralai": "Mistral AI",
    "moonshotai": "Moonshot AI",
    "nousresearch": "Nous Research",
    "nvidia": "NVIDIA",
    "01-ai": "01.AI",
    "openai": "OpenAI",
    "openai-codex": "OpenAI Codex",
    "opencode": "OpenCode",
    "openrouter": "OpenRouter",
    "perplexity": "Perplexity",
    "pygmalionai": "PygmalionAI",
    "qwen": "Qwen / Alibaba",
    "recursal": "Recursal",
    "sourceful": "Sourceful",
    "stepfun": "StepFun",
    "upstage": "Upstage",
    "vercel-ai-gateway": "Vercel AI Gateway",
    "x-ai": "xAI (Grok)",
    "xai": "xAI (Grok)",
    "z-ai": "Zhipu AI (GLM)",
    "zai": "Zhipu AI (GLM)",
    "arcee-ai": "Arcee AI",
    "sao10k": "Sao10k",
    "thedrummer": "TheDrummer",
    "eva-unit-01": "Eva Unit 01",
    "aetherwiing": "AetherWiing",
    "sophosympatheia": "Sophosympatheia",
}


def _prompt_price(m: dict) -> float:
    try:
        return float(m.get("pricing", {}).get("prompt", 999))
    except (TypeError, ValueError):
        return 999.0


def _price_str(p: float) -> str:
    if p == 0:
        return "FREE"
    per_m = p * 1_000_000
    if per_m < 0.01:
        return f"${per_m:.4f}/M"
    return f"${per_m:.2f}/M"


def _provider_of(model_id: str) -> str:
    """Extract provider slug from model ID (e.g. 'moonshotai/kimi-k2.5' → 'moonshotai')."""
    return model_id.split("/")[0] if "/" in model_id else "other"


def _provider_label(slug: str) -> str:
    return PROVIDER_NAMES.get(slug, slug.replace("-", " ").title())


async def _fetch_openrouter_models(search: str = "", free_only: bool = False) -> list[dict]:
    """Fetch models from OpenRouter API. Always filters out models > $3/M."""
    async with httpx.AsyncClient(timeout=15) as client:
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"} if OPENROUTER_API_KEY else {}
        resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    models = data.get("data", [])

    # Hard filter: never show models costing more than $3/M tokens
    models = [m for m in models if _prompt_price(m) <= PRICE_LIMIT]

    if free_only:
        models = [m for m in models if m.get("id", "").endswith(":free")]
    elif search and search.lower() not in ("all", ""):
        q = search.lower()
        models = [m for m in models if q in m.get("id", "").lower() or q in (m.get("name") or "").lower()]

    # Sort by price (free first), then alphabetically within provider
    models.sort(key=lambda m: (_prompt_price(m), m.get("id", "")))

    return models


def _build_model_keyboard(models: list[dict], user_data: dict) -> InlineKeyboardMarkup:
    """Build inline keyboard grouped by provider. Stores model list in user_data."""
    # Store ordered list for callback lookup
    model_ids = [m.get("id", "") for m in models]
    user_data["models_results"] = model_ids

    # Group by provider
    from collections import defaultdict
    groups: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for idx, m in enumerate(models):
        provider = _provider_of(m.get("id", ""))
        groups[provider].append((idx, m))

    rows = []
    for provider_slug in sorted(groups.keys(), key=_provider_label):
        entries = groups[provider_slug]
        label = _provider_label(provider_slug)
        # Provider header row (non-clickable)
        rows.append([InlineKeyboardButton(f"── {label} ──", callback_data="noop")])
        # Model buttons, 2 per row
        btn_row = []
        for idx, m in entries:
            mid = m.get("id", "")
            name = (m.get("name") or mid).split("/")[-1]  # short name
            price = _price_str(_prompt_price(m))
            btn_label = f"{name} · {price}"[:40]  # Telegram button label limit
            btn_row.append(InlineKeyboardButton(btn_label, callback_data=f"mpick:{idx}"))
            if len(btn_row) == 2:
                rows.append(btn_row)
                btn_row = []
        if btn_row:
            rows.append(btn_row)

    rows.append([InlineKeyboardButton("✖ Cancel", callback_data="mpick:cancel")])
    return InlineKeyboardMarkup(rows)


async def _show_model_picker(msg, models: list[dict], user_data: dict, title: str) -> None:
    """Send model picker with inline keyboard. Splits into multiple messages if needed."""
    if not models:
        await msg.reply_text("No models found matching that criteria.")
        return

    keyboard = _build_model_keyboard(models, user_data)

    # Telegram has a limit on keyboard size; if too many rows, split into pages
    # For now send with a note of total count
    provider_count = len(set(_provider_of(m.get("id", "")) for m in models))
    await msg.reply_text(
        f"*{title}*\n{len(models)} models across {provider_count} providers — tap to switch:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


def _set_vps_model(model_id: str) -> tuple[bool, str]:
    """SSH to VPS and update the active model. Returns (success, message)."""
    result = subprocess.run(
        ["ssh", VPS_SSH_HOST, f"/root/scripts/set-model.sh '{model_id}'"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip() or result.stdout.strip()


async def handle_vps_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Show live VPS health from Cloudflare Worker."""
    msg = update.message
    if not msg:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CF_WORKER_URL}/health")
            data = resp.json()
    except Exception as e:
        await msg.reply_text(f"❌ Could not reach watchdog: {e}")
        return

    alive = data.get("alive", False)
    age = data.get("last_seen_seconds_ago", "?")
    svcs = data.get("services", {})
    svc_lines = "\n".join(
        f"{'✅' if v == 'active' else '❌'} `{k}` — {v}"
        for k, v in svcs.items()
    )
    disk = data.get("disk_free_gb", "?")
    mem = data.get("mem_free_mb", "?")
    uptime = data.get("uptime", "?")
    ts = data.get("timestamp", "?")

    await msg.reply_text(
        f"{'🟢' if alive else '🔴'} *VPS Status* (seen {age}s ago)\n\n"
        f"{svc_lines}\n\n"
        f"💾 {disk}GB free | 🧠 {mem}MB free\n"
        f"⏱ Uptime: {uptime}",
        parse_mode="Markdown",
    )


async def handle_vps_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart [service] — Queue a restart command on the VPS via Cloudflare Worker."""
    msg = update.message
    if not msg:
        return

    args = context.args or []
    svc = args[0] if args else "all"
    cmd = f"restart:{svc}"

    valid = {"all", "gateway", "ollama", "postgres", "telegram-router"}
    if svc not in valid:
        await msg.reply_text(
            f"Unknown service `{svc}`. Valid options:\n" +
            "\n".join(f"  /restart {s}" for s in sorted(valid)),
            parse_mode="Markdown",
        )
        return

    if not CF_ADMIN_TOKEN:
        await msg.reply_text("❌ `CF_ADMIN_TOKEN` not set in `.env`", parse_mode="Markdown")
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{CF_WORKER_URL}/command",
                headers={"Authorization": f"Bearer {CF_ADMIN_TOKEN}"},
                json={"command": cmd},
            )
            data = resp.json()
    except Exception as e:
        await msg.reply_text(f"❌ Could not reach watchdog: {e}")
        return

    if data.get("ok"):
        await msg.reply_text(
            f"⚡ Queued `{cmd}` — will run on next VPS heartbeat (≤2 min).",
            parse_mode="Markdown",
        )
    else:
        await msg.reply_text(f"❌ {data.get('error', 'Unknown error')}")


async def handle_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/models [search] — Browse all affordable OpenRouter models grouped by provider."""
    msg = update.message
    if not msg:
        return

    args = context.args or []
    search = " ".join(args).strip()

    await msg.reply_text("🔍 Fetching models..." if search else "🔍 Loading all models under $3/M...")

    try:
        models = await _fetch_openrouter_models(search=search)
    except Exception as e:
        await msg.reply_text(f"❌ Error fetching models: {e}")
        return

    title = f'Models matching "{search}"' if search else "All models ≤ $3/M"
    await _show_model_picker(msg, models, context.user_data, title)


async def handle_models_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/modelsfree — Browse all free OpenRouter models grouped by provider."""
    msg = update.message
    if not msg:
        return

    await msg.reply_text("🆓 Fetching free models...")

    try:
        models = await _fetch_openrouter_models(free_only=True)
    except Exception as e:
        await msg.reply_text(f"❌ Error fetching models: {e}")
        return

    await _show_model_picker(msg, models, context.user_data, "Free models on OpenRouter")


async def handle_model_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for model picker inline buttons (mpick:<idx> or mpick:cancel)."""
    query = update.callback_query
    await query.answer()

    data = query.data  # "mpick:<idx>" or "mpick:cancel" or "noop"

    if data == "noop":
        return

    if data == "mpick:cancel":
        await query.edit_message_text("Cancelled.", reply_markup=InlineKeyboardMarkup([]))
        context.user_data.pop("models_results", None)
        return

    try:
        idx = int(data.split(":")[1])
    except (IndexError, ValueError):
        return

    results = context.user_data.get("models_results", [])
    if not results or idx >= len(results):
        await query.edit_message_text("Session expired. Run /models again.")
        return

    model_id = results[idx]
    await query.edit_message_text(
        f"⚡ Switching to `{model_id}`...",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([]),
    )

    success, output = _set_vps_model(model_id)
    if success:
        await query.message.reply_text(f"✅ Active model: `{model_id}`", parse_mode="Markdown")
    else:
        await query.message.reply_text(
            f"❌ Failed to switch:\n```\n{output[:400]}\n```", parse_mode="Markdown"
        )
    context.user_data.pop("models_results", None)


async def handle_model_search_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """No longer used for model picking (now buttons), kept for compatibility."""
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
