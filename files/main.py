import time
import logging
import sys
from datetime import datetime
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

# Report interval in seconds (every 30 minutes)
REPORT_INTERVAL = 1800

def main():
    logger.info("Starting Imbalance Trading Bot (Survival Mode)...")
    logger.info(f"Paper Trading: {Config.PAPER_TRADING}")
    if Config.PAPER_TRADING:
        logger.info(f"📊 Paper Trading Initial Balance: ${Config.PAPER_TRADING_INITIAL_BALANCE:.2f}")
    logger.info(f"Cost Limit: ${Config.COST_LIMIT_DAILY_USD}/day")

    strategy = ImbalanceStrategy()
    
    # Send startup message
    startup_msg = "Imbalance Bot is now online and scanning."
    if Config.PAPER_TRADING:
        startup_msg += f"\n📊 Paper Trading Mode: ${Config.PAPER_TRADING_INITIAL_BALANCE:.2f} starting balance"
    strategy.telegram.send_alert("Bot Started", startup_msg, "INFO")

    # Track last report time
    last_report_time = time.time()
    pipeline_count = 0

    try:
        while True:
            try:
                pipeline_count += 1
                logger.info(f"Running pipeline... (cycle #{pipeline_count})")
                strategy.run_pipeline()
                logger.info("Pipeline complete. Sleeping...")
                
                # Periodic reporting
                current_time = time.time()
                if current_time - last_report_time >= REPORT_INTERVAL:
                    logger.info("📊 Generating periodic trading report...")
                    if Config.PAPER_TRADING and strategy.paper_trading:
                        strategy.paper_trading.log_report()
                    else:
                        # Log basic state report for live trading
                        _log_live_trading_report(strategy)
                    last_report_time = current_time
                    
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                strategy.telegram.send_alert("Error", f"Bot crashed in main loop: {e}", "CRITICAL")
            
            # Sleep interval (e.g., 5 minutes to matches 5m candles or similar, avoids rate limits)
            # Imbalance strategy might need faster scanning, but we have limits.
            # Let's do 5 minutes (300s)
            time.sleep(300)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        # Final report on shutdown
        if Config.PAPER_TRADING and strategy.paper_trading:
            logger.info("📊 Final Trading Report:")
            strategy.paper_trading.log_report()
        strategy.telegram.send_alert("Bot Stopped", "User manually stopped the bot.", "INFO")

def _log_live_trading_report(strategy):
    """Log a report for live trading mode."""
    state = strategy.state.state
    perf = state.get("performance", {})
    capital = state.get("capital", {})
    
    logger.info("=" * 50)
    logger.info("📊 LIVE TRADING REPORT")
    logger.info("=" * 50)
    logger.info(f"💰 Capital: ${capital.get('current', 0):.2f}")
    logger.info(f"📈 Total PnL: ${perf.get('total_pnl', 0):.2f}")
    logger.info(f"🟢 Wins: {perf.get('win_count', 0)} | 🔴 Losses: {perf.get('loss_count', 0)}")
    logger.info(f"💸 API Costs: ${state.get('costs', {}).get('total_api_cost', 0):.2f}")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
