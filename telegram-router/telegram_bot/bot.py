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

from .handlers import handle_approve, handle_chatid, handle_message, handle_stop, handle_topics
from .project_router import ProjectRouter


async def post_init(app) -> None:
    """Set bot commands and warm the project router cache."""
    router: ProjectRouter = app.bot_data["router"]
    await router.init()

    await app.bot.set_my_commands([
        BotCommand("chatid",   "Show chat + thread IDs (for setup)"),
        BotCommand("topics",   "List all project → topic mappings"),
        BotCommand("stop",     "Interrupt the Claude session for this topic"),
        BotCommand("interrupt","Alias for /stop"),
    ])
    print("[bot] ready — polling for messages")


async def post_shutdown(app) -> None:
    router: ProjectRouter = app.bot_data["router"]
    await router.close()


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    db_url = os.environ["DATABASE_URL"]
    forum_id = int(os.getenv("TELEGRAM_FORUM_ID", "0"))

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
    app.add_handler(CommandHandler("chatid",    handle_chatid))
    app.add_handler(CommandHandler("topics",    handle_topics))
    app.add_handler(CommandHandler("stop",      handle_stop))
    app.add_handler(CommandHandler("interrupt", handle_stop))

    # Inline button callbacks (approve/deny from permission relay)
    app.add_handler(CallbackQueryHandler(handle_approve, pattern=r"^(approve|deny):"))

    # All other messages — route to tmux
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
