
import logging
import sys
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import anthropic

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from files.config import Config
from files.database import TradeDatabase
from files.llm_client import LLMClient
from files.state_manager import StateManager
from files.utils import get_trading_session


class ETFormatter(logging.Formatter):
    """Custom formatter that displays timestamps in Eastern Time with session info."""
    
    def formatTime(self, record, datefmt=None):
        et = ZoneInfo("America/New_York")
        dt = datetime.fromtimestamp(record.created, tz=et)
        session = get_trading_session()
        if datefmt:
            return dt.strftime(datefmt)
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S ET')} | {session}"


# Configure logging with ET time
formatter = ETFormatter('%(asctime)s | %(name)s - %(levelname)s - %(message)s')
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

class TradeAnalyzer:
    def __init__(self):
        self.db = TradeDatabase()
        self.state = StateManager()
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    
    def get_condition_matched_trades(
        self,
        symbol: str = None,
        regime: str = None,
        timeframe: str = None,
        limit: int = 30
    ) -> tuple[list, str]:
        """
        Fetch trades that match the current trading conditions.
        Tries SurrealDB first for graph-aware retrieval; falls back to
        the new SQLite condition-matched method; finally falls back to
        plain recency if all else fails.

        Returns:
            (trades: list, source: str)  where source is one of:
              "surrealdb"   — matched by conditions via SurrealDB
              "sqlite"      — matched by conditions via SQLite
              "sqlite_recent" — plain recency fallback (no conditions)
        """
        from files.surreal_client import SurrealClient
        surreal = SurrealClient()

        # --- PRIMARY: SurrealDB condition-matched ---
        try:
            trades = surreal.query_trades_by_conditions(
                symbol=symbol,
                regime=regime,
                timeframe=timeframe,
                limit=limit
            )
            if trades:
                logger.info(
                    f"[SurrealDB] Retrieved {len(trades)} condition-matched trades "
                    f"(symbol={symbol}, regime={regime}, timeframe={timeframe})"
                )
                return trades, "surrealdb"
            logger.info("[SurrealDB] condition query returned 0 trades — using SQLite fallback")
        except Exception as e:
            logger.warning(f"[SurrealDB] condition query failed ({e}) — using SQLite fallback")

        # --- SECONDARY: SQLite condition-matched ---
        try:
            trades = self.db.get_trades_by_conditions(
                symbol=symbol,
                regime=regime,
                timeframe=timeframe,
                limit=limit
            )
            if trades:
                logger.info(
                    f"[SQLite] Retrieved {len(trades)} condition-matched trades"
                )
                return trades, "sqlite"
        except Exception as e:
            logger.warning(f"[SQLite] condition query failed ({e})")

        # --- TERTIARY: plain recency (original behaviour) ---
        logger.info("[SQLite] Falling back to plain recent trades (no conditions)")
        return self.db.get_recent_trades(limit=50), "sqlite_recent"
        
    def generate_analysis_prompt(self, trades):
        """
        Formats trades into a prompt for Opus.
        """
        if not trades:
            return None
            
        trades_text = ""
        for t in trades:
            trades_text += f"""
---
Trade ID: {t['id']}
Symbol: {t['symbol']} ({t['timeframe']})
Side: {t['side']}
Result: {'WIN' if t['pnl'] > 0 else 'LOSS'} (${t['pnl']:.2f} | {t['pnl_percent']:.2f}%)
Entry Reason: {t['entry_reason']}
Exit Reason: {t['exit_reason']}
Regime: {t['regime']}
Market Context (Snapshot): {t['market_context']}
Analysis Data: {t['analysis_context']}
---
"""
        prompt = f"""You are a senior trading systems architect tasked with optimizing an automated trading bot.
Below is a log of recent trades executed by the bot. 
Your goal is to analyze these trades to identify patterns, weaknesses, and areas for improvement.

**TRADE LOGS:**
{trades_text}

**TASK:**
1. Analyze the performance. key metrics: Win Rate, Avg R:R, dominant failure patterns.
2. Identify specific market conditions (Regime/Context) where the bot performs poorly.
3. Review the 'Analysis Data' (LLM's reasoning) vs the actual outcome. Did the LLM hallucinate a setup? Was the logic sound but the market unpredictable?
4. Propose 3 CONCRETE, IMPLEMENTABLE improvements. These should be code changes, logic updates, or prompt refinements.

**OUTPUT FORMAT:**
Return a Markdown document with the following sections:

# Performance Review
(Summary of metrics and observations)

# Weakness Identification
(What is going wrong? Be specific.)

# Improvement Plan
(Fact-based recommendations)
1. **[Action Item Name]**: [Description of change]
2. **[Action Item Name]**: [Description of change]
3. **[Action Item Name]**: [Description of change]

# Implementation Notes
(Technical details for the developer)
"""
        return prompt

    def run_analysis(
        self,
        symbol: str = None,
        regime: str = None,
        timeframe: str = None
    ):
        """
        Run trade analysis. If symbol/regime/timeframe are provided,
        fetches condition-matched trades for a targeted report.
        If called with no args, behaves identically to before (recent 50).
        """
        any_conditions = any([symbol, regime, timeframe])

        if any_conditions:
            logger.info(
                f"Fetching condition-matched trades "
                f"(symbol={symbol}, regime={regime}, timeframe={timeframe})..."
            )
            trades, source = self.get_condition_matched_trades(
                symbol=symbol, regime=regime, timeframe=timeframe, limit=30
            )
        else:
            logger.info("Fetching recent trades (no conditions specified)...")
            trades = self.db.get_recent_trades(limit=50)
            source = "sqlite_recent"

        if not trades:
            logger.info("No closed trades found to analyze.")
            return

        logger.info(f"Analyzing {len(trades)} trades via {source} with Opus...")
        prompt = self.generate_analysis_prompt(trades)

        # Inject retrieval context into prompt header so Opus knows what it
        # is looking at
        if any_conditions:
            filter_desc = ", ".join(
                f"{k}={v}" for k, v in
                [("symbol", symbol), ("regime", regime), ("timeframe", timeframe)]
                if v
            )
            prompt = (
                f"[TARGETED ANALYSIS — conditions: {filter_desc} | "
                f"source: {source} | n={len(trades)}]\n\n" + prompt
            )

        try:
            message = self.client.messages.create(
                model=Config.ANALYSIS_MODEL,
                max_tokens=4000,
                system="You are a simplified, high-level trading optimization assistant.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            report = message.content[0].text
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{symbol}_{regime}".replace("/", "-") if any_conditions else ""
            filename = f"implementation_plan_recommendation{suffix}_{timestamp}.md"
            filepath = os.path.join(Config.DATA_DIR, filename)
            
            with open(filepath, "w") as f:
                f.write(report)
                
            logger.info(f"Analysis complete! Report saved to {filepath}")
            print(f"\nAnalysis complete. Report saved to: {filepath}")
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Analyze bot trades")
    parser.add_argument("--symbol",   default=None, help="e.g. BTC/USDT")
    parser.add_argument("--regime",   default=None, help="e.g. TRENDING_UP")
    parser.add_argument("--timeframe", default=None, help="e.g. daily")
    args = parser.parse_args()

    analyzer = TradeAnalyzer()
    analyzer.run_analysis(
        symbol=args.symbol,
        regime=args.regime,
        timeframe=args.timeframe
    )
