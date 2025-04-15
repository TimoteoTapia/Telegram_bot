# telegram_app.py
import logging
import os
import threading
import signal
import sys
import time
import requests
from flask import Flask, request, jsonify
from telegram import Update
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

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app to handle webhook and keep service alive on Render
flask_app = Flask(__name__)
telegram_app = None

# Get render service URL from environment or use default local URL for development
RENDER_SERVICE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")

# Keep track of bot status
bot_healthy = True
last_activity_time = time.time()


@flask_app.route("/")
def home():
    global last_activity_time
    last_activity_time = time.time()
    return "Bot is running on Render!"


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global last_activity_time
    last_activity_time = time.time()

    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        logger.info(f"Received update: {update.update_id}")

        # Process the update
        telegram_app.process_update(update)
        return "OK"
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        return jsonify({"error": str(e)}), 500


# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    print("Shutting down gracefully...")
    if telegram_app:
        # Remove webhook before shutting down
        telegram_app.bot.delete_webhook()
        # Stop the telegram application
        telegram_app.stop()
    sys.exit(0)


def keep_alive():
    """
    Periodically pings our own service to prevent Render from hibernating it.
    Also monitors the bot's health.
    """
    global bot_healthy, last_activity_time

    while True:
        try:
            current_time = time.time()
            # Ping our own service every 10 minutes
            response = requests.get(RENDER_SERVICE_URL)
            logger.info(f"Keep-alive ping sent. Status: {response.status_code}")

            # Check if there's been activity in the last 30 minutes
            if current_time - last_activity_time > 1800:  # 30 minutes
                logger.warning(
                    "No activity detected for 30 minutes, checking bot health..."
                )

                # Try to get bot info as a health check
                try:
                    bot_info = telegram_app.bot.get_me()
                    logger.info(f"Bot is healthy: {bot_info.username}")
                    bot_healthy = True
                except Exception as e:
                    logger.error(f"Bot health check failed: {e}")
                    bot_healthy = False

                    # Try to re-establish webhook if bot is unhealthy
                    try:
                        webhook_url = f"{RENDER_SERVICE_URL}/webhook"
                        telegram_app.bot.set_webhook(webhook_url)
                        logger.info(f"Webhook re-established at {webhook_url}")
                    except Exception as webhook_err:
                        logger.error(f"Failed to re-establish webhook: {webhook_err}")

                # Reset activity timer after health check
                last_activity_time = current_time

        except Exception as e:
            logger.error(f"Error in keep-alive function: {e}")

        # Sleep for 10 minutes before next ping
        time.sleep(600)


def setup_webhook():
    """Configure the webhook for the Telegram bot"""
    webhook_url = f"{RENDER_SERVICE_URL}/webhook"
    try:
        # Delete any existing webhook first
        telegram_app.bot.delete_webhook()
        time.sleep(1)  # Small delay to ensure webhook is removed

        # Set the new webhook
        telegram_app.bot.set_webhook(webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return False


def run_flask():
    """Run the Flask app to handle webhook requests"""
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)


def setup_handlers():
    """Set up all the conversation handlers for the bot"""
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


def init_bot():
    """Initialize the Telegram bot application"""
    global telegram_app
    try:
        # Create the Application
        telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

        # Set up conversation handlers
        setup_handlers()

        # Set up webhook
        if setup_webhook():
            logger.info("Bot initialized successfully with webhook")
        else:
            logger.error("Failed to initialize bot with webhook")
    except Exception as e:
        logger.error(f"Error initializing bot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize the bot
    init_bot()

    # Start the keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()

    # Run the Flask app in the main thread
    logger.info("Starting webhook server...")
    run_flask()
