import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID

    def send_message(self, text):
        if not self.token or not self.chat_id:
            logger.warning("Telegram token or chat_id not set. Skipping notification.")
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def send_alert(self, title, message, level="INFO"):
        emojis = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "CRITICAL": "🚨",
            "SUCCESS": "✅",
            "TRADE": "🚀",
            "ANALYSIS": "🧠"
        }
        emoji = emojis.get(level, "ℹ️")
        formatted_message = f"{emoji} *{title}*\n\n{message}"
        self.send_message(formatted_message)
