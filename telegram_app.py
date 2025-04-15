# telegram_app.py
import logging
import os
import threading
import signal
import sys
import time
import requests
from flask import Flask, request, jsonify, Response
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
bot_initialization_completed = False
webhook_retry_count = 0
MAX_WEBHOOK_RETRIES = 5


@flask_app.route("/")
def home():
    global last_activity_time
    last_activity_time = time.time()
    status = "healthy" if bot_healthy else "unhealthy"
    return f"Bot is running on Render! Status: {status}"


@flask_app.route("/health")
def health_check():
    global bot_healthy
    if bot_healthy:
        return Response(status=200)
    else:
        return Response(status=503)


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global last_activity_time, bot_healthy
    last_activity_time = time.time()

    if not telegram_app:
        logger.error("Webhook received but telegram_app is not initialized")
        return jsonify({"error": "Bot not initialized"}), 503

    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        if update:
            logger.info(f"Received update: {update.update_id}")
            # Process the update asynchronously
            threading.Thread(target=process_update_safe, args=(update,)).start()
            bot_healthy = True
            return "OK"
        else:
            logger.warning("Received empty update")
            return "No update data", 400
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        return jsonify({"error": str(e)}), 500


def process_update_safe(update):
    """Process update in a thread-safe manner with proper error handling"""
    try:
        # Use application's dispatcher to process the update
        telegram_app.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update {update.update_id}: {e}")


# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    logger.info("Shutting down gracefully...")
    if telegram_app:
        try:
            # Remove webhook before shutting down
            telegram_app.bot.delete_webhook()
            # Stop the telegram application
            telegram_app.stop()
            logger.info("Bot stopped successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
    sys.exit(0)


def keep_alive():
    """
    Periodically pings our own service to prevent Render from hibernating it.
    Also monitors the bot's health and reinitializes if necessary.
    """
    global bot_healthy, last_activity_time, bot_initialization_completed, webhook_retry_count

    while True:
        try:
            current_time = time.time()

            # Ping our own service every 5 minutes (reduced from 10 minutes to prevent hibernation)
            try:
                response = requests.get(f"{RENDER_SERVICE_URL}/health", timeout=30)
                logger.info(f"Keep-alive ping sent. Status: {response.status_code}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Self ping failed: {e}")

            # Check if there's been activity in the last 20 minutes (reduced from 30)
            if current_time - last_activity_time > 1200:  # 20 minutes
                logger.warning(
                    "No activity detected for 20 minutes, checking bot health..."
                )

                # Try to get bot info as a health check
                try:
                    bot_info = telegram_app.bot.get_me()
                    logger.info(f"Bot is healthy: {bot_info.username}")
                    bot_healthy = True
                    webhook_retry_count = 0  # Reset retry counter on success
                except Exception as e:
                    logger.error(f"Bot health check failed: {e}")
                    bot_healthy = False

                    # Try to re-establish webhook if bot is unhealthy
                    if webhook_retry_count < MAX_WEBHOOK_RETRIES:
                        try:
                            webhook_url = f"{RENDER_SERVICE_URL}/webhook"
                            # Delete any existing webhook first
                            telegram_app.bot.delete_webhook()
                            time.sleep(1)  # Small delay
                            # Set the new webhook
                            telegram_app.bot.set_webhook(webhook_url)
                            logger.info(f"Webhook re-established at {webhook_url}")
                            webhook_retry_count += 1
                        except Exception as webhook_err:
                            logger.error(
                                f"Failed to re-establish webhook: {webhook_err}"
                            )
                    else:
                        logger.critical(
                            "Max webhook retry attempts reached. Attempting full bot reinitialization."
                        )
                        # Try full reinitialization if webhook keeps failing
                        try:
                            if telegram_app:
                                telegram_app.bot.delete_webhook()
                                telegram_app.stop()
                            init_bot()
                            webhook_retry_count = (
                                0  # Reset counter after reinitialization
                            )
                        except Exception as reinit_err:
                            logger.critical(f"Failed to reinitialize bot: {reinit_err}")

                # Reset activity timer after health check
                last_activity_time = current_time

        except Exception as e:
            logger.error(f"Error in keep-alive function: {e}")

        # Sleep for 5 minutes before next ping (reduced from 10 minutes)
        time.sleep(300)


def setup_webhook():
    """Configure the webhook for the Telegram bot with retry mechanism"""
    global webhook_retry_count

    webhook_url = f"{RENDER_SERVICE_URL}/webhook"
    max_attempts = 3
    retry_delay = 5  # seconds

    for attempt in range(1, max_attempts + 1):
        try:
            # Delete any existing webhook first
            telegram_app.bot.delete_webhook()
            time.sleep(1)  # Small delay to ensure webhook is removed

            # Set the new webhook
            telegram_app.bot.set_webhook(webhook_url)
            logger.info(f"Webhook set to {webhook_url} (attempt {attempt})")
            webhook_retry_count = 0  # Reset counter on success
            return True
        except Exception as e:
            logger.error(
                f"Failed to set webhook (attempt {attempt}/{max_attempts}): {e}"
            )
            if attempt < max_attempts:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                return False


def run_flask():
    """Run the Flask app to handle webhook requests"""
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


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
        name="main_conversation",
        persistent=False,
    )

    # Add the conversation handler
    telegram_app.add_handler(conv_handler)

    # Add a fallback handler for general messages
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )


def init_bot():
    """Initialize the Telegram bot application"""
    global telegram_app, bot_initialization_completed

    try:
        # Create the Application with optimized settings for Render
        telegram_app = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .connect_timeout(15.0)
            .read_timeout(15.0)
            .write_timeout(15.0)
            .pool_timeout(15.0)
            .build()
        )

        # Set up conversation handlers
        setup_handlers()

        # Set up webhook
        if setup_webhook():
            logger.info("Bot initialized successfully with webhook")
            bot_initialization_completed = True
        else:
            logger.error("Failed to initialize bot with webhook")
            bot_initialization_completed = False
    except Exception as e:
        logger.error(f"Error initializing bot: {e}")
        bot_initialization_completed = False
        # Don't exit - keep trying again


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
