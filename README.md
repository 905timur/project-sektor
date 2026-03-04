# Project Sektor | Dual-tier Autonomous Trading System

> **⚠️ IMPORTANT: This bot is for educational and research purposes only.** A
> Python-based crypto trading bot that identifies and trades **imbalance
> patterns** (Fair Value Gaps and Order Blocks) in cryptocurrency markets using
> initial programmatic screening and AI-powered analysis.

## Quick Start

### Prerequisites

- Python 3.11+
- API keys (see [`.env.example`](.env.example))

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   # For trading bot
   pip install -r files/requirements.txt

   # For UI (optional)
   pip install fastapi uvicorn jinja2
   ```
3. Copy `.env.example` to `.env` and add your API keys

### Running the Bot

```bash
# Run the main trading bot
python files/main.py

# Run the web UI (optional)
python -m ui.main
# Then open http://localhost:8000
```

> **Tip**: If running on a VPS or remote server, always run the bot inside a
> **tmux** or **screen** session to keep it running after you disconnect:
>
> ```bash
> # Start a new tmux session
> tmux new -s trading-bot
>
> # Run the bot
> python files/main.py
>
> # Detach from session (press Ctrl+B, then D)
> # To reattach later: tmux attach -t trading-bot
> ```

## Project Structure

| Directory                                          | Description                   |
| -------------------------------------------------- | ----------------------------- |
| [`files/`](files/)                                 | Core trading bot code         |
| [`files/strategy.py`](files/strategy.py)           | Main trading strategy         |
| [`files/llm_client.py`](files/llm_client.py)       | AI client (Claude + DeepSeek) |
| [`files/paper_trading.py`](files/paper_trading.py) | Paper trading simulation      |
| [`ui/`](ui/)                                       | Web dashboard (FastAPI)       |
| [`tests/`](tests/)                                 | Test suite                    |

## Features

- **Imbalance Detection**: Identifies Fair Value Gaps (FVGs) and Order Blocks
  (OBs)
- **Multi-Timeframe Analysis**: Daily and Weekly timeframe patterns
- **AI-Powered Screening**: DeepSeek R1 for initial screening, Claude Opus for
  detailed analysis
- **Risk Management**: Strict position sizing, stop-loss, and take-profit
  controls
- **Paper Trading**: Test with simulated funds before using real money
- **Web Dashboard**: Monitor bot status and manage settings via UI

## Configuration

The bot can be configured via the **web UI** or **environment variables**.

### Web UI (Recommended)

The easiest way to configure the bot is via the built-in web dashboard:

```bash
# Run the web UI
python -m ui.main
# Then open http://localhost:8000
```

The web UI provides:

- **Dashboard**: Real-time view of bot activity, open positions, and trade
  history
- **Settings**: Configure all bot parameters including:
  - Trading pairs (e.g., BTC/USDT, ETH/USDT)
  - Risk settings (max position size, stop-loss %, take-profit %)
  - AI model selection and API keys
  - Paper trading vs live trading mode
  - Daily API cost limits
  - Notification preferences

Navigate to the **Settings** page after starting the UI to configure your
preferences. Changes take effect immediately without restarting the bot.

### Environment Variables

Alternatively, configuration can be managed via environment variables in the
`.env` file. See [`.env.example`](.env.example) for available options.

Key settings in [`files/config.py`](files/config.py):

- `PAPER_TRADING`: Use paper trading mode (default: true)
- `MAX_DAILY_COST`: Maximum daily API cost limit
- Position sizing and risk parameters

## Documentation

See repository [WIKI.md](WIKI.md) for detailed documentation.

## License

See [LICENSE](LICENSE) for details.
