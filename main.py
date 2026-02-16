import time
import logging
import sys
from strategy import ImbalanceStrategy
from config import Config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Config.LOG_FILE)
    ]
)

logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Imbalance Trading Bot (Survival Mode)...")
    logger.info(f"Paper Trading: {Config.PAPER_TRADING}")
    logger.info(f"Cost Limit: ${Config.COST_LIMIT_DAILY_USD}/day")

    strategy = ImbalanceStrategy()
    
    # Send startup message
    strategy.telegram.send_alert("Bot Started", "Imbalance Bot is now online and scanning.", "INFO")

    try:
        while True:
            try:
                logger.info("Running pipeline...")
                strategy.run_pipeline()
                logger.info("Pipeline complete. Sleeping...")
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                strategy.telegram.send_alert("Error", f"Bot crashed in main loop: {e}", "CRITICAL")
            
            # Sleep interval (e.g., 5 minutes to matches 5m candles or similar, avoids rate limits)
            # Imbalance strategy might need faster scanning, but we have limits.
            # Let's do 5 minutes (300s)
            time.sleep(300)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        strategy.telegram.send_alert("Bot Stopped", "User manually stopped the bot.", "INFO")

if __name__ == "__main__":
    main()
