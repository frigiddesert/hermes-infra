"""
telegram_bot/bot.py — Entry point. Registers all handlers and starts polling.

Usage:
    python -m telegram_bot.bot
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

load_dotenv()

from .handlers import (
    handle_approve, handle_chatid, handle_message, handle_models,
    handle_models_free, handle_model_pick_callback, handle_stop, handle_topics,
    handle_vps_status, handle_vps_restart,
)
from .project_router import ProjectRouter


async def post_init(app) -> None:
    """Set bot commands and warm the project router cache."""
    router: ProjectRouter = app.bot_data["router"]
    await router.init()

    await app.bot.set_my_commands([
        BotCommand("chatid",      "Show chat + thread IDs (for setup)"),
        BotCommand("topics",      "List all project → topic mappings"),
        BotCommand("stop",        "Interrupt the Claude session for this topic"),
        BotCommand("interrupt",   "Alias for /stop"),
        BotCommand("models",      "Browse & switch OpenRouter models"),
        BotCommand("modelsfree", "Pick from live free models"),
        BotCommand("status",      "VPS health check"),
        BotCommand("restart",     "Restart VPS service: /restart [gateway|ollama|all]"),
    ])
    print("[bot] ready — polling for messages")


async def post_shutdown(app) -> None:
    router: ProjectRouter = app.bot_data["router"]
    await router.close()


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    db_url = os.environ["DATABASE_URL"]
    forum_id = int(os.getenv("TELEGRAM_FORUM_ID") or "0")

    if not forum_id:
        print(
            "[bot] WARNING: TELEGRAM_FORUM_ID not set.\n"
            "      Add the bot to your forum group and run /chatid to discover it,\n"
            "      then set TELEGRAM_FORUM_ID in .env and restart."
        )

    router = ProjectRouter(database_url=db_url)

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Inject shared state
    app.bot_data["router"] = router
    app.bot_data["forum_id"] = forum_id

    # Commands
    app.add_handler(CommandHandler("chatid",      handle_chatid))
    app.add_handler(CommandHandler("topics",      handle_topics))
    app.add_handler(CommandHandler("stop",        handle_stop))
    app.add_handler(CommandHandler("interrupt",   handle_stop))
    app.add_handler(CommandHandler("models",      handle_models))
    app.add_handler(CommandHandler("modelsfree", handle_models_free))
    app.add_handler(CommandHandler("status",      handle_vps_status))
    app.add_handler(CommandHandler("restart",     handle_vps_restart))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_approve, pattern=r"^(approve|deny):"))
    app.add_handler(CallbackQueryHandler(handle_model_pick_callback, pattern=r"^mpick:"))

    # All other messages — route to tmux
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
