# telegram_app.py
import logging
import os
import threading
import time
import requests
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
)
from src.app.handlers import (
    start,
    handle_callback,
    handle_message,
    CHOOSING_ACTION,
    ENTERING_DATE,
    CONFIRMING_DATE,
    ENTERING_NAME,
    SELECTING_EVENT,
    ENTERING_NEW_DATE,
)
from src.app.config import TELEGRAM_BOT_TOKEN
from flask import Flask

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variables
telegram_app = None
flask_app = Flask(__name__)  # Simple Flask app to keep service alive
last_activity_time = time.time()

# Service URL (useful for keep-alive)
RENDER_SERVICE_URL = os.environ.get(
    "RENDER_SERVICE_URL", "https://telegram-bot-rvl0.onrender.com"
)


@flask_app.route("/")
def home():
    global last_activity_time
    last_activity_time = time.time()
    return "Bot server is running! Bot is using polling method, not webhook."


@flask_app.route("/ping")
def ping():
    return "pong"


def keep_alive():
    """Periodically ping our own service to prevent Render from hibernating"""
    while True:
        try:
            # Ping our own service every 5 minutes
            response = requests.get(f"{RENDER_SERVICE_URL}/ping")
            logger.info(f"Keep-alive ping sent. Status: {response.status_code}")
        except Exception as e:
            logger.error(f"Error in keep-alive function: {e}")

        # Sleep for 5 minutes before next ping
        time.sleep(300)


def run_flask():
    """Run the Flask app to keep the service alive"""
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


def run_bot():
    """Initialize and run the Telegram bot with polling"""
    global telegram_app

    # Build the application with adjusted timeout settings
    telegram_app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(10.0)
        .read_timeout(10.0)
        .write_timeout(10.0)
        .build()
    )

    # Define conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(handle_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            ],
            ENTERING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            ],
            CONFIRMING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            ],
            ENTERING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            ],
            SELECTING_EVENT: [
                CallbackQueryHandler(handle_callback),
            ],
            ENTERING_NEW_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    # Add the conversation handler
    telegram_app.add_handler(conv_handler)

    # Add a fallback handler for general messages
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot is running in polling mode...")

    # Start polling with improved settings
    telegram_app.run_polling(
        drop_pending_updates=True,  # Don't process updates from when the bot was offline
        allowed_updates=[
            "message",
            "callback_query",
        ],  # Specify what updates to receive
        poll_interval=1.0,  # Poll every 1 second
        timeout=30,  # Timeout for long polling
    )


if __name__ == "__main__":
    # Start the Flask app in a separate thread to keep the service alive
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Start the keep-alive pinger in another thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()

    # Run the Telegram bot in the main thread
    run_bot()
