# Imbalance Trading Bot (Survival Mode)

A Python-based crypto trading bot that identifies and trades "imbalance"
patterns in cryptocurrency markets using a two-tier AI system. The bot is
designed for capital preservation with strict risk controls.

## What This Bot Does

This bot scans cryptocurrency markets for **imbalance patterns** — situations
where price has moved significantly away from its equilibrium (e.g., extreme RSI
readings, volume spikes, Bollinger Band breaches, or large deviations from the
50-period EMA). When detected, it uses AI to analyze the opportunity and execute
trades autonomously.

### Two Types of Imbalances

| Type                 | Timeframe | Description                                        | Target Hold |
| -------------------- | --------- | -------------------------------------------------- | ----------- |
| **Daily Imbalance**  | 1D        | Price extended from EMA, RSI extreme, volume spike | 1-2 days    |
| **Weekly Imbalance** | 1W        | Structural shift, support/resistance breach        | ~1 week     |

The bot can hold **one Daily position** and **one Weekly position
simultaneously**.

---

## How It Works: The Decision Pipeline

The bot runs a continuous pipeline every 5 minutes. Here's the complete flow:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MAIN PIPELINE LOOP                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. CHECK DAILY LIMITS                                                       │
│     └─> Reset daily API costs if new day                                    │
│                                                                              │
│  2. MANAGE EXISTING POSITIONS                                               │
│     └─> Check SL/TP for open daily & weekly positions                        │
│     └─> Close if stop-loss or take-profit hit                               │
│                                                                              │
│  3. CHECK POSITION LIMITS                                                    │
│     └─> If both daily & weekly positions open → skip scanning               │
│                                                                              │
│  4. SCAN FOR OPPORTUNITIES                                                   │
│     └─> For each trading pair:                                              │
│         │                                                                    │
│         ├─► A. FETCH MARKET DATA                                            │
│         │    └─> OHLCV + Technical Indicators (RSI, BB, MACD, ATR, EMA)   │
│         │                                                                    │
│         ├─► B. TECHNICAL FILTER (Cost Saver)                               │
│         │    └─> Check if ANY of:                                           │
│         │        • RSI < 30 or > 70 (extreme)                             │
│         │        • Price > 5% from EMA50 (extended)                        │
│         │        • Volume > 2x 20-period SMA (spike)                       │
│         │        • Bollinger Band breach                                   │
│         │    └─> Must have RSI extreme + (volume spike OR BB breach OR    │
│         │        extended)                                                  │
│         │    └─> If NOT → skip this pair (saves API calls)                │
│         │                                                                    │
│         ├─► C. SONNET ANALYSIS (Claude 3.5 Sonnet)                        │
│         │    └─> Format last 10 candles as CSV (token efficient)          │
│         │    └─> Send to LLM with system prompt defining imbalance types  │
│         │    └─> LLM returns JSON:                                          │
│         │        {                                                          │
│         │          "signal": "BUY"|"SELL"|"NEUTRAL",                       │
│         │          "confidence": "HIGH"|"MEDIUM"|"LOW",                    │
│         │          "imbalance_type": "DAILY"|"WEEKLY"|"NONE",              │
│         │          "reasoning": "..."                                      │
│         │          "entry_target": float,                                  │
│         │          "stop_loss": float,                                     │
│         │          "take_profit": float                                    │
│         │        }                                                          │
│         │    └─> If signal != "BUY" OR confidence != "HIGH" → skip        │
│         │                                                                    │
│         └─► D. EXECUTE TRADE (Autonomous)                                  │
│              └─> Check spread safety (< 0.5%)                               │
│              └─> Calculate position size (45% of capital)                │
│              └─> Place market order (or post-only limit)                  │
│              └─> Save position to state with SL/TP                         │
│              └─> Send Telegram notification                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tools & Technologies Used

### Exchange Interaction

- **[CCXT](https://ccxt.com/)** - Unified library for crypto exchange APIs
  - Used for: Fetching OHLCV, tickers, order books, balances, placing orders
  - Exchange: Crypto.com (spot trading)

### Technical Analysis

- **[Pandas-TA](https://twopirllc.github.io/pandas-ta/)** - Technical analysis
  indicators
  - **RSI (14)** - Relative Strength Index for overbought/oversold
  - **Bollinger Bands (20,2)** - Volatility bands
  - **MACD (12,26,9)** - Trend momentum
  - **ATR (14)** - Average True Range for stop loss calculation
  - **EMA (50)** - Exponential moving average for extension calculation
  - **Volume SMA (20)** - For volume spike detection

### AI Decision Making

- **[Anthropic Claude](https://www.anthropic.com/)** - LLM for trading decisions
  - **Claude 3.5 Sonnet** (`claude-3-5-sonnet-20240620`)
    - Role: Opportunity Analyst
    - Task: Analyze market data, identify imbalances, output trade signals
    - Uses prompt caching for cost efficiency
  - **Claude 3 Opus** (reserved for approval)
    - Role: Risk Manager
    - Task: Critique trades, provide final approval, adjust stop losses

### Notifications

- **Telegram Bot API** - Real-time alerts for:
  - Bot startup/shutdown
  - Opportunity detection
  - Trade execution
  - Position closes (SL/TP)
  - Errors

### Data Management

- **JSON state file** - Persistent storage for:
  - Capital tracking
  - Open positions (daily/weekly)
  - P&L performance
  - API cost tracking (daily/total)

---

## Decision-Making Tools Explained

### 1. Technical Filter (Pre-LLM)

Before spending money on LLM API calls, the bot uses simple technical checks:

| Indicator              | Threshold           | Purpose                                   |
| ---------------------- | ------------------- | ----------------------------------------- |
| RSI                    | < 30 or > 70        | Oversold/overbought conditions            |
| Extension (from EMA50) | > 5%                | Price significantly extended from average |
| Volume                 | > 2x 20-period SMA  | Unusual volume activity                   |
| Bollinger Bands        | Price outside bands | Volatility squeeze/breakout               |

**Logic:** Must have RSI extreme AND (volume spike OR BB breach OR extension)

### 2. Sonnet Analysis

The LLM receives:

- Last 10 candles as CSV (columns: timestamp, open, high, low, close, volume,
  rsi, atr, extension)
- System prompt defining the two imbalance types
- Returns structured JSON with signal, confidence, entry/stop/target

### 3. Execution Filters

- **Spread Check**: Bid-ask spread must be < 0.5%
- **Position Size**: 45% of available capital per trade
- **Max Positions**: 1 daily + 1 weekly (never more)

---

## Risk Protections

| Protection           | Value       | Description                               |
| -------------------- | ----------- | ----------------------------------------- |
| **Daily Cost Limit** | $1.00 max   | Stops LLM calls if API costs exceed limit |
| **Spread Limit**     | 0.5% max    | Aborts trade if bid-ask spread too wide   |
| **Max Drawdown**     | 10% weekly  | Loss limit to prevent large drawdowns     |
| **Position Size**    | 45% capital | Leaves buffer for managing positions      |
| **Paper Trading**    | Default ON  | All trades simulated unless disabled      |

---

## Configuration

All settings are in [`config.py`](config.py):

```python
# Trading Pairs
PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]

# Timeframes
TIMEFRAMES = {"daily": "1d", "weekly": "1w"}

# Position Sizing
POSITION_SIZE_PERCENT = 0.45  # 45% per position

# Risk Controls
SPREAD_LIMIT_PERCENT = 0.5
COST_LIMIT_DAILY_USD = 1.00
MAX_DRAWDOWN_WEEKLY_PERCENT = 0.10
```

---

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Variables

Create a `.env` file in the parent directory with:

```env
# Exchange API (Crypto.com)
CRYPTO_COM_API_KEY=your_api_key
CRYPTO_COM_API_SECRET=your_api_secret

# AI Provider
ANTHROPIC_API_KEY=your_anthropic_key

# Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Run

```bash
python main.py
```

The bot starts in **Paper Trading mode** by default. To enable real trading:

```python
# In config.py, change:
PAPER_TRADING = False
```

---

## File Structure

```
imbalance-bot/
├── main.py              # Entry point, main loop
├── config.py            # All configuration parameters
├── strategy.py          # ImbalanceStrategy class - the brain
├── exchange_client.py   # CCXT wrapper for Crypto.com
├── market_data.py       # OHLCV fetching + indicator calculation
├── llm_client.py        # Anthropic API wrapper with cost tracking
├── state_manager.py     # JSON-based state persistence
├── telegram_bot.py      # Telegram notification handler
├── requirements.txt     # Python dependencies
└── data/
    └── bot_state.json   # Persistent state file
```

---

## State Management

The bot maintains state in [`data/bot_state.json`](data/bot_state.json):

```json
{
   "capital": { "initial": 0, "current": 0, "currency": "USDT" },
   "positions": {
      "daily": null,
      "weekly": null
   },
   "performance": {
      "total_pnl": 0,
      "win_count": 0,
      "loss_count": 0,
      "weekly_loss": 0
   },
   "costs": {
      "total_api_cost": 0,
      "daily_api_cost": 0,
      "last_reset_date": "2024-01-01"
   }
}
```

---

## Example Flow

1. **Bot starts** → Sends Telegram alert "Bot Started"
2. **5-minute timer** → Pipeline runs
3. **Technical filter** → Finds BTC/USDT with RSI 75 + volume spike
4. **Sonnet analysis** → Returns
   `{"signal": "BUY", "confidence": "HIGH", "entry_target": 52000, "stop_loss": 50000, "take_profit": 55000}`
5. **Execution** → Checks spread (0.2%), places order, saves position
6. **Next cycle** → Checks position → Price hit TP → Closes position, records
   P&L, sends alert

---

## Cost Efficiency

The bot optimizes for low API costs:

- **Technical filter** prevents unnecessary LLM calls (~90% of pairs filtered)
- **CSV formatting** reduces tokens by 40-50% vs JSON
- **Prompt caching** (Anthropic) gives 90% discount on cache reads
- **Daily cost limit** ($1) prevents runaway spending
