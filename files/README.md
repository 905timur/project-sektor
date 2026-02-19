# Imbalance Trading Bot (Survival Mode)

A Python-based crypto trading bot that identifies and trades **imbalance
patterns** (Fair Value Gaps and Order Blocks) in cryptocurrency markets using
AI-powered analysis. The bot is designed for capital preservation with strict
risk controls.

## What This Bot Does

This bot scans cryptocurrency markets for **imbalance patterns** — Fair Value
Gaps (FVGs) and Order Blocks (OBs) where price is likely to retrace and reverse.
When detected, it uses AI to analyze the opportunity and execute trades
autonomously.

### Two Types of Imbalances

| Type                 | Timeframe | Description                                             | Target Hold |
| -------------------- | --------- | ------------------------------------------------------- | ----------- |
| **Daily Imbalance**  | 1D        | FVG/OB on daily chart, scanned with 4H precision        | 1-2 days    |
| **Weekly Imbalance** | 1W        | Major FVG/OB on weekly chart, scanned with 1D precision | ~1 week     |

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
│  3. CHECK WATCHLIST (Priority)                                              │
│     └─> For each tracked opportunity:                                       │
│         │                                                                    │
│         ├─► A. FETCH MULTI-TIMEFRAME DATA                                   │
│         │    └─> Primary TF (1D/1W) + Context TF (4H/1D)                    │
│         │    └─> Technical Indicators (RSI, BB, MACD, ATR, EMA, ADX)       │
│         │                                                                    │
│         ├─► B. CHECK RETRACEMENT                                            │
│         │    └─> Has price entered the FVG/OB zone?                         │
│         │    └─> If YES → Trigger Analysis                                  │
│         │                                                                    │
│         ├─► C. DEEPSEEK SCREENING (DeepSeek R1 via OpenRouter)             │
│         │    └─> Quick go/no-go decision (30s timeout)                     │
│         │    └─> Format data as CSV (token efficient)                      │
│         │    └─> Include market regime + S/R levels                         │
│         │    └─> Returns: {signal, confidence, proceed_to_full_analysis}   │
│         │    └─> Log screening result to database                           │
│         │    └─> If proceed=true → Escalate to Opus                        │
│         │    └─> If proceed=false → Skip Opus, stay on watchlist           │
│         │                                                                    │
│         └─► D. OPUS ANALYSIS (Claude Opus 4.5)                             │
│              └─> Only if DeepSeek approved (proceed=true)                  │
│              └─> Format data as CSV (token efficient)                       │
│              └─> Include market regime + S/R levels                          │
│              └─> Fetch news sentiment (non-blocking, 15s timeout)           │
│              └─> LLM returns JSON:                                          │
│                  {                                                          │
│                    "signal": "BUY"|"SELL"|"NEUTRAL",                       │
│                    "confidence": "HIGH"|"MEDIUM"|"LOW",                     │
│                    "imbalance_type": "DAILY"|"WEEKLY"|"NONE",              │
│                    "scores": {...},                                         │
│                    "reasoning": "...",                                       │
│                    "entry_target": float,                                    │
│                    "stop_loss": float,                                      │
│                    "take_profit": float                                     │
│                  }                                                          │
│              └─> If signal != "BUY"/"SELL" OR confidence != "HIGH" → skip │
│              └─> Execute trade with regime-based position sizing            │
│                                                                              │
│  4. SCAN FOR NEW OPPORTUNITIES (if capacity exists)                         │
│     └─> For each trading pair:                                             │
│         │                                                                    │
│         ├─► A. FETCH MARKET DATA                                           │
│         │    └─> OHLCV + Technical Indicators                              │
│         │                                                                    │
│         ├─► B. DETECT IMBALANCE STRUCTURES                                 │
│         │    └─> Fair Value Gaps (bullish/bearish)                         │
│         │    └─> Order Blocks (bullish/bearish)                            │
│         │    └─> If found → Add to Watchlist                               │
│         │                                                                    │
│         └─► C. FALLBACK EXTREME CHECK                                       │
│              └─> RSI extreme + (volume spike OR extension)                  │
│              └─> If found → Add to Watchlist                               │
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
  - **ADX (14)** - Trend strength indicator
  - **Volume SMA (20)** - For volume spike detection

### AI Decision Making

- **[DeepSeek R1](https://deepseek.com/)** - Free LLM for pre-screening (via
  OpenRouter)
  - **Model**: `deepseek/deepseek-r1-0528:free`
  - Role: Quick go/no-go screener
  - Task: Determine if setup warrants full Opus analysis
  - Timeout: 30 seconds (free tier)
  - Cost: **FREE** (via OpenRouter)
  - Falls back to Opus on timeout/failure

- **[Anthropic Claude](https://www.anthropic.com/)** - LLM for trading decisions
  - **Claude Opus 4.5** (`claude-opus-4-5-20251101`)
    - Role: Full Opportunity Analyst (only after DeepSeek approval)
    - Task: Analyze market data, identify imbalances, output trade signals
    - Uses prompt caching for cost efficiency

### News Sentiment Analysis

- **RSS News Client** - Real-time crypto news aggregation
  - **Sources**: CoinTelegraph, CoinDesk, Decrypt, The Block, Bitcoin Magazine,
    NewsBTC, CryptoPotato
  - **Sentiment Analysis**: Bullish/bearish keyword detection
  - **Integration**: News context added to LLM analysis prompts
  - **Caching**: 5-minute in-memory cache for performance

### Notifications

- **Telegram Bot API** - Real-time alerts for:
  - Bot startup/shutdown
  - Opportunity detection
  - Trade execution
  - Position closes (SL/TP)
  - Errors

### Data Management

- **SQLite Database** - Persistent storage for:
  - Trade history with entry/exit details
  - Market context snapshots
  - Analysis data for each trade

- **JSON state file** - Persistent storage for:
  - Capital tracking
  - Open positions (daily/weekly)
  - Watchlist of opportunities
  - P&L performance
  - API cost tracking (daily/total)
  - Paper trading state

- **JSON beliefs file** - Self-reflection beliefs for AI context:
  - Lessons learned from closed trades
  - Written by Opus after each trade
  - Used in subsequent analysis prompts
  - Mirrored in SurrealDB with graph edges to source trades
  - **Belief scoring**: Relevance based on regime match, symbol match, timeframe
    match, and recency
  - **Injected per analysis**: Up to 5 relevant beliefs injected into each LLM
    prompt

---

## Key Features

### 1. Fair Value Gap (FVG) Detection

Detects unfilled price gaps where:

- **Bullish FVG**: Candle 1 High < Candle 3 Low (gap up)
- **Bearish FVG**: Candle 1 Low > Candle 3 High (gap down)

Minimum gap size: 0.1%

### 2. Order Block (OB) Detection

Identifies institutional order blocks:

- **Bullish OB**: Last bearish candle before a strong move up (>2x ATR)
- **Bearish OB**: Last bullish candle before a strong move down (>2x ATR)

### 3. Market Regime Detection

Classifies market conditions for position sizing:

- **TRENDING_UP**: ADX > 25, price above EMA50
- **TRENDING_DOWN**: ADX > 25, price below EMA50
- **VOLATILE**: ATR > 1.5x average ATR
- **RANGING**: Default state

### 4. Support/Resistance Identification

Uses pivot point detection (5-candle window) to identify key levels for:

- Take profit targets
- Stop loss placement
- Entry timing

### 5. Multi-Timeframe Analysis

| Position Type | Primary TF | Context TF |
| ------------- | ---------- | ---------- |
| Daily         | 1D         | 4H         |
| Weekly        | 1W         | 1D         |

### 6. Regime-Based Position Sizing

- **Normal conditions**: 45% of capital per position
- **Volatile market**: 22.5% (50% reduction)
- **Counter-trend trades**: 22.5% (50% reduction)

### 7. News Sentiment Integration

Real-time news sentiment analysis integrated into trading decisions:

- **7 RSS Sources**: CoinTelegraph, CoinDesk, Decrypt, The Block, Bitcoin
  Magazine, NewsBTC, CryptoPotato
- **Sentiment Detection**: Bullish/bearish keyword analysis with confidence
  scoring
- **Smart Filtering**: BTC-specific news for Bitcoin pairs, general crypto news
  for others
- **Non-Blocking**: 15-second timeout ensures news never delays trade execution
- **Deduplication**: Jaccard similarity (0.8 threshold) removes duplicate
  headlines

Example news sentiment output in LLM prompt:

```
NEWS SENTIMENT (last 5 articles):
Overall: BEARISH (MEDIUM confidence) | 23% bullish, 26% bearish
Headlines:
- Bitcoin remains under pressure near $68,000 even as panic ebbs (CoinDesk)
- Top Expert Projects Bitcoin Bear Market To End In Less Than 365 Days (NewsBTC)
...
```

### 8. DeepSeek Pre-Screening Layer

Two-stage AI analysis for cost optimization:

- **Stage 1 - DeepSeek R1 (Free)**:
  - Quick go/no-go decision before expensive Opus call
  - Uses `deepseek/deepseek-r1-0528:free` via OpenRouter
  - 30-second timeout (free tier)
  - Returns: signal, confidence, proceed_to_full_analysis
  - Logs screening result to database
  - Falls back to Opus on timeout/failure

- **Stage 2 - Claude Opus 4.5**:
  - Only called if DeepSeek approves (proceed=true)
  - Full analysis with news sentiment
  - More expensive but more thorough

**Screening Logic**:

| DeepSeek Signal | Confidence  | Action            |
| --------------- | ----------- | ----------------- |
| BUY/SELL        | HIGH/MEDIUM | Proceed to Opus   |
| BUY/SELL        | LOW         | Stay on watchlist |
| NEUTRAL         | Any         | Stay on watchlist |
| Timeout/Failure | -           | Fall back to Opus |

**Logging Prefixes**:

- 🔍 DeepSeek approved - escalating to Opus
- ⏭️ DeepSeek screened out - Opus skipped
- ⚠️ DeepSeek failed - falling back to Opus
- 💬 DeepSeek reasoning output

---

## Risk Protections

| Protection           | Value       | Description                               |
| -------------------- | ----------- | ----------------------------------------- |
| **Daily Cost Limit** | $1.00 max   | Stops LLM calls if API costs exceed limit |
| **Spread Limit**     | 0.5% max    | Aborts trade if bid-ask spread too wide   |
| **Min Volume**       | $1M USD min | Aborts trade if 24h volume too low        |
| **Max Drawdown**     | 10% weekly  | Loss limit to prevent large drawdowns     |
| **Position Size**    | 45% capital | Leaves buffer for managing positions      |
| **Paper Trading**    | Default ON  | All trades simulated unless disabled      |

---

## Trading Session Stats

The bot tracks performance metrics per trading session for funnel analysis:

| Metric                  | Description                                         |
| ----------------------- | --------------------------------------------------- |
| **Zones Entered**       | Opportunities that retraced into the imbalance zone |
| **Rejected (Candle)**   | Failed rejection candle confirmation gate           |
| **Rejected (Volume)**   | Failed volume confirmation gate                     |
| **Rejected (DeepSeek)** | DeepSeek pre-screening said no                      |
| **Rejected (Opus)**     | Claude Opus analysis said no                        |
| **Trades Executed**     | Successfully executed trades                        |

Sessions are tracked by market hours (Eastern Time):

| Session        | Hours (ET)  |
| -------------- | ----------- |
| 🌏 ASIA        | 8 PM - 5 AM |
| 🇬🇧 LONDON      | 3 AM - 8 AM |
| 🇬🇧🇺🇸 LONDON/NY | 8 AM - 1 PM |
| 🇺🇸 NEW YORK    | 1 PM - 5 PM |

Session reports are automatically sent via Telegram when the session changes.

---

## Rejection Filters

Before executing a trade, the bot applies multiple confirmation gates:

### 1. Rejection Candle Confirmation

After price retraces into an imbalance zone, the bot waits for a confirmation
candle:

- **Bullish Setup**: Next candle must show bullish momentum (close higher than
  previous)
- **Bearish Setup**: Next candle must show bearish momentum (close lower than
  previous)

### 2. Volume Confirmation

The confirmation candle must have above-average volume (>20-period SMA) to
validate institutional interest.

### 3. DeepSeek Pre-Screening

Free LLM screening layer provides initial go/no-go decision (see DeepSeek
Pre-Screening Layer section).

---

## AI Position Management

The bot actively manages open positions using AI reviews:

### Review Triggers

| Trigger            | Description                                           |
| ------------------ | ----------------------------------------------------- |
| **Danger Zone**    | Price within 0.5% of stop loss                        |
| **Milestone 1R**   | Position reached 1x risk (100% of SL distance) profit |
| **Milestone 2R**   | Position reached 2x risk (200% of SL distance) profit |
| **Stale Position** | 50% of time elapsed with <0.5% profit                 |

### AI Review Cooldowns

| Model        | Cooldown   | Description                                    |
| ------------ | ---------- | ---------------------------------------------- |
| **DeepSeek** | 30 minutes | Minimum time between DeepSeek position reviews |
| **Opus**     | 6 hours    | Minimum time between Opus position reviews     |

### AI Actions

When triggered, AI can:

- Confirm holding the position
- Suggest a new stop loss (trailing stop optimization)
- Recommend closing early (take profit or stop loss)

---

## Volume Profile Analysis

The bot analyzes volume distribution to identify high-probability zones:

- **Volume Nodes**: Price levels with high trading activity
- **Volume Gaps**: Areas with low volume (likely to fill)
- **Volume Spike Detection**: >2x average volume indicates institutional
  activity

Volume analysis is used alongside imbalance detection to prioritize
high-probability setups.

## Configuration

All settings are in [`config.py`](config.py):

```python
# Trading Pairs
PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]

# Timeframes
TIMEFRAMES = {"daily": "1d", "weekly": "1w"}

# Multi-Timeframe Context
MULTI_TIMEFRAME_MAPPING = {"daily": "4h", "weekly": "1d"}

# Position Sizing
POSITION_SIZE_PERCENT = 0.45  # 45% per position

# Risk Controls
SPREAD_LIMIT_PERCENT = 0.5
COST_LIMIT_DAILY_USD = 1.00
MAX_DRAWDOWN_WEEKLY_PERCENT = 0.10

# Imbalance Detection
IMBALANCE_PARAMS = {
    "fvg_lookback": 20,
    "ob_lookback": 50,
    "min_fvg_size_percent": 0.5,
    "retracement_threshold": 0.5
}

# Paper Trading
PAPER_TRADING = True
PAPER_TRADING_INITIAL_BALANCE = 50.0
PAPER_SLIPPAGE_RATE_MIN = 0.001   # 0.1% minimum slippage
PAPER_SLIPPAGE_RATE_MAX = 0.005   # 0.5% maximum slippage
PAPER_FEE_RATE = 0.004            # 0.4% fee per trade

# Volume Filter
MIN_VOLUME_USD = 1000000          # Minimum 24h volume ($1M)

# AI Position Management
POSITION_DEEPSEEK_COOLDOWN_SECS = 1800   # 30 minutes
POSITION_OPUS_COOLDOWN_SECS = 21600      # 6 hours
POSITION_DANGER_ZONE_PERCENT = 0.5       # 0.5% from SL triggers danger
POSITION_MILESTONE_1R = 1.0             # First profit milestone
POSITION_MILESTONE_2R = 2.0             # Second profit milestone
POSITION_STALE_TIME_THRESHOLD = 0.5      # 50% of time elapsed
POSITION_STALE_PNL_THRESHOLD = 0.5      # <0.5% gain = stale

# Self-Reflection
BELIEFS_INJECTED_PER_ANALYSIS = 5   # Beliefs to inject in prompts
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

# AI Providers
ANTHROPIC_API_KEY=your_anthropic_key
OPENROUTER_API_KEY=your_openrouter_key  # For DeepSeek screening (free)

# Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# SurrealDB Shadow Layer (optional)
# Uses embedded KV store by default - no separate server needed
SURREALDB_URL=surrealkv://data/surreal.db   # embedded (default)
# SURREALDB_URL=ws://localhost:8000         # standalone server
```

---

## SurrealDB Data Layer

The bot uses SurrealDB as a secondary data store that mirrors SQLite and JSON
writes, thengradually replaces them as the primary read source for AI context
retrieval.

### Architecture: Dual-Write with Progressive Read Migration

| Phase                    | Writes                           | Reads                                         |
| ------------------------ | -------------------------------- | --------------------------------------------- |
| **Phase 1** (Dual-Write) | SQLite/JSON + SurrealDB (shadow) | SQLite/JSON only                              |
| **Phase 2** (Read Layer) | SQLite/JSON + SurrealDB (shadow) | **SurrealDB-first** → fallback to SQLite/JSON |

### Phase 1: Dual-Write Shadow Layer

When enabled, every write operation is duplicated to SurrealDB:

| Record Type | SurrealDB Record | Graph Edge                                    |
| ----------- | ---------------- | --------------------------------------------- |
| Trade       | `trade:{id}`     | —                                             |
| Belief      | `belief:{id}`    | `belief:{id}->learned_from->trade:{trade_id}` |
| Screening   | `screening:{id}` | —                                             |
| Bot State   | `state:bot`      | —                                             |

The graph edge (`belief->learned_from->trade`) captures which trade a belief was
learned from, enabling regime-based belief retrieval in Phase 2.

### Phase 2: Read Layer for AI Context

Phase 2 upgrades two hot paths that feed context into Opus:

#### 1. Belief Retrieval (for trade analysis prompts)

- **Before**: In-memory Python scoring over `beliefs.json` based on tag matching
- **After**: SurrealDB graph query that filters by the regime of the _source
  trade_

```python
# Graph traversal ranks beliefs by source trade regime
SELECT * FROM belief
WHERE symbol = $symbol
  AND timeframe = $timeframe
ORDER BY regime_match DESC, timestamp DESC
```

This is semantically richer — a belief earns relevance from what actually
happened, not from how it was labeled.

#### 2. Trade Context for Analysis (analyze_trades.py)

- **Before**: Blindly fetch 50 most recent closed trades
- **After**: Condition-matched retrieval (same symbol + regime + timeframe)

```bash
# CLI usage
python analyze_trades.py --symbol BTC/USDT --regime TRENDING_UP --timeframe daily
```

The query progressively loosens filters if too few results: exact match →
regime+timeframe only → regime only → recency fallback.

### Fallback Behavior

All SurrealDB operations have automatic fallbacks:

| Operation                  | Fallback                             |
| -------------------------- | ------------------------------------ |
| Belief query fails         | JSON scoring over `beliefs.json`     |
| Belief query returns empty | JSON scoring over `beliefs.json`     |
| Trade query fails          | SQLite `get_trades_by_conditions()`  |
| Trade query returns empty  | SQLite `get_recent_trades(limit=50)` |

Fallbacks are transparent — callers receive identical data shapes regardless of
which source was used. Logs always indicate the source at INFO level:

```
[SurrealDB] Loaded 5 beliefs for BTC/USDT/daily regime=TRENDING_UP (3 regime-matched)
[SurrealDB] condition query returned 0 trades — using SQLite fallback
[SQLite] Falling back to plain recent trades (no conditions)
```

### Configuration

| Variable            | Default                       | Description                   |
| ------------------- | ----------------------------- | ----------------------------- |
| `SURREALDB_URL`     | `surrealkv://data/surreal.db` | Embedded KV store (no server) |
|                     | `ws://localhost:8000`         | Standalone SurrealDB server   |
| `READ_TIMEOUT_SECS` | `3`                           | Max wait for SurrealDB reads  |

### Data Files

```
imbalance-bot/
...
├── surreal_client.py    # SurrealDB client (read + write)
...
└── data/
    ├── bot_state.json   # Persistent state (JSON)
    ├── trades.db       # SQLite trade database
    ├── beliefs.json    # Self-reflection beliefs (JSON)
    └── surreal.db     # SurrealDB embedded store
```

### Enabling/Disabling

SurrealDB is optional. If the package is not installed or `SURREALDB_URL` is
empty, the bot gracefully degrades to SQLite/JSON-only operation.

### 3. Run

```bash
python main.py
```

The bot starts in **Paper Trading mode** by default with a $50 simulated
balance. To enable real trading:

```python
# In config.py, change:
PAPER_TRADING = False
```

---

## File Structure

```
imbalance-bot/
├── main.py              # Entry point, main loop with session tracking
├── config.py            # All configuration parameters
├── strategy.py          # ImbalanceStrategy class - the brain
├── exchange_client.py   # CCXT wrapper for Crypto.com
├── market_data.py       # OHLCV + indicators + FVG/OB detection
├── llm_client.py        # Anthropic API wrapper with cost tracking + news integration
├── news_client.py       # RSS news aggregator with sentiment analysis
├── state_manager.py     # JSON-based state persistence
├── opportunity_tracker.py # Watchlist management for imbalances
├── database.py          # SQLite trade logging
├── surreal_client.py   # SurrealDB client (dual-write + read layer)
├── paper_trading.py     # Paper trading simulation manager
├── telegram_bot.py      # Telegram notification handler
├── analyze_trades.py   # Trade history analysis tool (with condition-matched retrieval)
├── belief_manager.py    # Self-reflection belief management
├── requirements.txt     # Python dependencies
└── data/
    ├── bot_state.json   # Persistent state file
    ├── trades.db       # SQLite trade database
    ├── beliefs.json    # Self-reflection beliefs (JSON)
    └── surreal.db     # SurrealDB embedded store
```

---

## State Management

The bot maintains state in `data/bot_state.json`:

```json
{
  "capital": { "initial": 0, "current": 0, "currency": "USDT" },
  "positions": {
    "daily": null,
    "weekly": null
  },
  "watching": {
    "BTC/USDT_daily": {
      "symbol": "BTC/USDT",
      "timeframe": "daily",
      "imbalance_type": "bullish",
      "zone_top": 52000,
      "zone_bottom": 51000,
      "bias": "bullish",
      "stage": "watching"
    }
  },
  "performance": {
    "total_pnl": 0,
    "win_count": 0,
    "loss_count": 0,
    "weekly_loss": 0
  },
  "paper_trading": {
    "initial_balance": 50.0,
    "balance": 50.0,
    "available_balance": 50.0,
    "realized_pnl": 0.0,
    "trades_executed": 0,
    "winning_trades": 0,
    "losing_trades": 0
  },
  "costs": {
    "total_api_cost": 0,
    "daily_api_cost": 0,
    "last_reset_date": "2024-01-01"
  }
}
```

---

## Trade Database

All trades are logged to SQLite (`data/trades.db`) with:

| Field            | Description                 |
| ---------------- | --------------------------- |
| id               | Unique trade ID             |
| symbol           | Trading pair                |
| timeframe        | daily/weekly                |
| side             | buy/sell                    |
| entry_price      | Entry price                 |
| exit_price       | Exit price (when closed)    |
| size             | Position size               |
| pnl              | Profit/loss in USD          |
| pnl_percent      | Profit/loss percentage      |
| entry_time       | Unix timestamp of entry     |
| exit_time        | Unix timestamp of exit      |
| entry_reason     | Why the trade was entered   |
| exit_reason      | Why the trade was exited    |
| stop_loss        | SL price                    |
| take_profit      | TP price                    |
| regime           | Market regime at entry      |
| market_context   | JSON snapshot of indicators |
| analysis_context | JSON of LLM analysis        |
| status           | OPEN/CLOSED                 |

### Screening Log

DeepSeek screening results are also logged to SQLite (`data/trades.db`):

| Field             | Description                       |
| ----------------- | --------------------------------- |
| id                | Unique screening ID               |
| timestamp         | Unix timestamp                    |
| symbol            | Trading pair                      |
| timeframe         | daily/weekly                      |
| model             | Model used (deepseek-r1)          |
| signal            | BUY/SELL/NEUTRAL                  |
| confidence        | HIGH/MEDIUM/LOW                   |
| reasoning         | Brief explanation                 |
| proceed           | 1 if approved for Opus, 0 if not  |
| prompt_tokens     | Token count (logged, not charged) |
| completion_tokens | Token count (logged, not charged) |
| raw_response      | Full model JSON response          |
| escalated_to_opus | 1 if Opus was called, 0 if not    |

---

## Example Flow

1. **Bot starts** → Sends Telegram alert "Bot Started"
2. **5-minute timer** → Pipeline runs
3. **Scan market** → Detects bullish FVG on BTC/USDT daily
4. **Add to watchlist** → Tracks zone ($51,000 - $52,000)
5. **Next cycle** → Price retraces into zone
6. **Opus analysis** → Returns HIGH confidence BUY signal
7. **Execution** → Checks regime (TRENDING_UP), calculates size, places order
8. **Next cycles** → Monitors position for SL/TP
9. **Exit** → Price hits TP → Closes position, logs to DB, sends alert

---

## Cost Efficiency

The bot optimizes for low API costs:

- **DeepSeek pre-screening** - Free tier filters out weak setups before Opus
- **Watchlist-based approach** - Only analyzes when price retraces into zones
- **CSV formatting** - Reduces tokens by 40-50% vs JSON
- **Prompt caching** (Anthropic) - 90% discount on cache reads
- **Daily cost limit** ($1) - Prevents runaway spending
- **Dual model** - DeepSeek (free) → Opus (paid) only when approved

---

## Paper Trading

The bot includes a comprehensive paper trading system:

- **Simulated balance tracking** - Starting at $50 by default
- **Position management** - Tracks paper positions separately (daily + weekly)
- **P&L calculation** - Real-time profit/loss tracking with fees
- **Trade history** - Full history of simulated trades with duration
- **Periodic reports** - Every 30 minutes
- **Slippage simulation** - 0.1% to 0.5% random slippage based on volatility
- **Fee simulation** - 0.4% Crypto.com spot fee per trade
- **Unrealized PnL** - Live tracking of open position profit/loss
- **Win rate tracking** - Tracks winning vs losing trades

---

## Logging

The bot uses Eastern Time (ET) timestamps with trading session tracking:

```
2024-01-15 09:30:00 ET | 🇺🇸 NEW YORK | strategy - INFO - Running pipeline...
```

Sessions tracked:

- 🌏 ASIA: 8 PM - 5 AM ET
- 🇬🇧 LONDON: 3 AM - 8 AM ET
- 🇬🇧🇺🇸 LONDON/NY OVERLAP: 8 AM - 1 PM ET
- 🇺🇸 NEW YORK: 1 PM - 5 PM ET

---

## Additional Documentation

- [`QUICK_START.md`](QUICK_START.md) - Quick start guide
- [`OPTIMIZATION_GUIDE.md`](OPTIMIZATION_GUIDE.md) - Cost optimization
  strategies
- [`CHANGES_SUMMARY.md`](CHANGES_SUMMARY.md) - Recent changes summary
