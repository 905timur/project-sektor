import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

class Config:
    # --- API Keys ---
    CRYPTO_COM_API_KEY = os.getenv("CRYPTO_COM_API_KEY")
    CRYPTO_COM_API_SECRET = os.getenv("CRYPTO_COM_API_SECRET")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    
    # --- OpenRouter API for DeepSeek Screening ---
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    DEEPSEEK_MODEL = "deepseek/deepseek-r1-0528:free"
    DEEPSEEK_TIMEOUT = 30  # seconds — free tier can be slow

    # --- Models ---
    ANALYSIS_MODEL = "claude-opus-4-5-20251101"
    # Sonnet removed as per strategy update

    # --- Trading Parameters ---
    PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]
    
    # Primary Timeframes (for execution)
    TIMEFRAMES = {
        "daily": "1d",   # Daily positions
        "weekly": "1w"   # Weekly positions
    }
    
    # Multi-Timeframe Context (Primary -> Context)
    MULTI_TIMEFRAME_MAPPING = {
        "daily": "4h",   # Daily positions scan 4H for precise entries
        "weekly": "1d"   # Weekly positions scan 1D for precise entries
    }

    # --- Imbalance Detection Parameters ---
    IMBALANCE_PARAMS = {
        "fvg_lookback": 20,       # Look back 20 candles for FVGs
        "ob_lookback": 50,        # Look back 50 candles for Order Blocks
        "min_fvg_size_percent": 0.5, # Minimum size of gap to consider
        "retracement_threshold": 0.5 # Price must retrace at least 50% into zone
    }
    
    # Capital Allocation
    # Allows 2 positions: 1 Daily, 1 Weekly
    MAX_DAILY_POSITIONS = 1
    MAX_WEEKLY_POSITIONS = 1
    
    # Position Sizing (Fixed amount or percentage of capital)
    # For survival mode with small capital, we might use fixed small amounts or percentage
    # Let's use percentage for scalability
    POSITION_SIZE_PERCENT = 0.45  # 45% per position (leaving 10% cash buffer)

    # --- Protections & Limits ---
    SPREAD_LIMIT_PERCENT = 0.5  # Max 0.5% spread allowed
    MIN_VOLUME_USD = 1000000    # Minimum 24h volume
    
    # Loss Limits
    MAX_DRAWDOWN_WEEKLY_PERCENT = 0.10  # 10% weekly loss limit
    
    # API Costs
    COST_LIMIT_DAILY_USD = 1.00 # Max daily spend on API

    # Paper Trading
    PAPER_TRADING = True # Default to True for safety
    PAPER_TRADING_INITIAL_BALANCE = 50.0  # Starting paper trading balance in USD
    
    # Paper Trading Realism (Slippage & Fees)
    PAPER_SLIPPAGE_RATE_MIN = 0.001  # 0.1% minimum slippage
    PAPER_SLIPPAGE_RATE_MAX = 0.005  # 0.5% maximum slippage in volatile conditions
    PAPER_FEE_RATE = 0.004           # 0.4% fee per trade (Crypto.com spot fee)

    # Paths
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
    STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")
    DB_FILE = os.path.join(DATA_DIR, "trades.db")
    LOG_FILE = os.path.join(DATA_DIR, "bot.log")
    AGENT_LOG_FILE = os.path.join(DATA_DIR, "agent.log")

    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)
