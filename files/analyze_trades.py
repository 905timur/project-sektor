
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


def get_trading_session():
    """Determine current trading session based on ET time."""
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    hour = now.hour
    
    if 20 <= hour or hour < 3:
        return "🌏 ASIA"
    elif 3 <= hour < 8:
        return "🇬🇧 LONDON"
    elif 8 <= hour < 13:
        return "🇬🇧🇺🇸 LONDON/NY OVERLAP"
    elif 13 <= hour < 17:
        return "🇺🇸 NEW YORK"
    else:
        return "🇺🇸 NEW YORK (AFTERNOON)"


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

    def run_analysis(self):
        logger.info("Fetching recent trades...")
        trades = self.db.get_recent_trades(limit=50)
        
        if not trades:
            logger.info("No closed trades found to analyze.")
            return

        logger.info(f"Analyzing {len(trades)} trades with Opus...")
        prompt = self.generate_analysis_prompt(trades)
        
        try:
            message = self.client.messages.create(
                model=Config.ANALYSIS_MODEL,
                max_tokens=4000,
                system="You are a simplified, high-level trading optimization assistant.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            report = message.content[0].text
            
            # Save report
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"implementation_plan_recommendation_{timestamp}.md"
            filepath = os.path.join(Config.DATA_DIR, filename)
            
            with open(filepath, "w") as f:
                f.write(report)
                
            logger.info(f"Analysis complete! Report saved to {filepath}")
            print(f"\nAnalysis complete. Report saved to: {filepath}")
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}")

if __name__ == "__main__":
    analyzer = TradeAnalyzer()
    analyzer.run_analysis()
