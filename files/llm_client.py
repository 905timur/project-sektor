import anthropic
import logging
import json
import os
import pandas as pd
from config import Config
from state_manager import StateManager

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self, state_manager):
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        self.state_manager = state_manager
        
        # Cached system prompts (built once, reused with caching)
        self._analysis_system_prompt = None
        self._approval_system_prompt = None

    def _track_cost(self, model, usage):
        """
        Calculates and tracks the cost of the API call.
        Now includes cached token pricing (90% discount for cache reads).
        """
        if not usage:
            return 0.0
            
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        
        # Get cached token counts (90% discount)
        cache_read_tokens = getattr(usage, 'cache_read_input_tokens', 0)
        cache_creation_tokens = getattr(usage, 'cache_creation_input_tokens', 0)
        
        # approximate costs (per 1M tokens) - Updated manually
        # Sonnet (3.5): $3 / $15 input/output, cache read: $0.30, cache write: $3.75
        # Opus (3): $15 / $75 input/output, cache read: $1.50, cache write: $18.75
        
        cost = 0.0
        if "sonnet" in model:
            # Regular input tokens (not cached)
            regular_input = input_tokens - cache_read_tokens - cache_creation_tokens
            cost = (regular_input / 1_000_000 * 3.00)
            # Cache read tokens (90% discount)
            cost += (cache_read_tokens / 1_000_000 * 0.30)
            # Cache creation tokens (one-time cost, 25% premium)
            cost += (cache_creation_tokens / 1_000_000 * 3.75)
            # Output tokens
            cost += (output_tokens / 1_000_000 * 15.00)
        elif "opus" in model:
            regular_input = input_tokens - cache_read_tokens - cache_creation_tokens
            cost = (regular_input / 1_000_000 * 15.00)
            cost += (cache_read_tokens / 1_000_000 * 1.50)
            cost += (cache_creation_tokens / 1_000_000 * 18.75)
            cost += (output_tokens / 1_000_000 * 75.00)
        
        # Log savings from caching
        if cache_read_tokens > 0:
            savings = cache_read_tokens / 1_000_000 * (3.00 if "sonnet" in model else 15.00) * 0.9
            logger.info(f"Cache savings: ${savings:.4f} ({cache_read_tokens} tokens)")
        
        self.state_manager.add_cost(cost)
        return cost

    def _format_market_data_csv(self, df):
        """
        Format market data as CSV for token efficiency.
        CSV is ~40-50% fewer tokens than JSON.
        """
        # Select only essential columns
        essential_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 
                          'rsi', 'atr', 'extension']
        
        # Filter to available columns
        available_cols = [c for c in essential_cols if c in df.columns]
        
        # Get last 10 rows
        recent = df[available_cols].tail(10)
        
        # Convert timestamp to string format for readability
        if 'timestamp' in recent.columns:
            recent['timestamp'] = recent['timestamp'].astype(str)
        
        # Format numbers to reduce token count (2-4 decimal places)
        for col in recent.columns:
            if col != 'timestamp':
                recent[col] = recent[col].apply(lambda x: round(float(x), 4) if pd.notna(x) else 0)
        
        return recent.to_csv(index=False)

    def _get_analysis_system_prompt(self, symbol, timeframe):
        """
        Returns the cached system prompt for analysis (Opus Enhanced).
        """
        return [
            {
                "type": "text",
                "text": f"""You are an expert imbalance trader analyzing cryptocurrency markets. Your goal is to identify high-probability setups where:

1. Price made a strong directional move creating an imbalance (fair value gap, liquidity void, or order block)
2. Price has retraced into the imbalance zone
3. Price is showing signs of rejection and continuation

**IMBALANCE TYPES:**

DAILY IMBALANCE (Short-term, hours to 1 day hold):
- Timeframe: {timeframe} (Context: higher timeframe)
- Setup: Strong move creates FVG/OB, price retraces 50-75% into zone
- Entry: When price shows rejection in the zone (bullish candle in bull zone, or vice versa)
- Target: Next structure level or 1.5-2x risk

WEEKLY IMBALANCE (Long-term, ~1 week hold):
- Timeframe: {timeframe}
- Setup: Major move creates large imbalance zone, price retraces into it
- Entry: Strong rejection from zone
- Target: Major structure level or 3-4x risk

**OUTPUT FORMAT:**

Return ONLY valid JSON:
{{
  "signal": "BUY" | "SELL" | "NEUTRAL",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "imbalance_type": "DAILY" | "WEEKLY" | "NONE",
  "scores": {{
    "imbalance_quality": 0-10,
    "retracement_quality": 0-10,
    "market_context": 0-10,
    "risk_reward": 0-10,
    "overall": 0-10
  }},
  "reasoning": "2-3 sentence explanation...",
  "entry_target": float,
  "stop_loss": float,
  "take_profit": float,
  "invalidation_level": float
}}

**RULES:**
- Only return HIGH confidence if overall score >= 8 AND all individual scores >= 7
- NEVER enter at the extreme (RSI 80 or 20) -> we want the RETRACEMENT entry
- If no clear imbalance zone exists, return NEUTRAL
- Daily positions stop < 3% away, Weekly stop < 7% away
- Risk:Reward must be > 1.5 (Daily) or > 3.0 (Weekly)""",
                "cache_control": {"type": "ephemeral"}
            }
        ]

    def analyze_opportunity(self, symbol, timeframe, market_data_dict, context_extras=None):
        """
        Uses Opus to analyze the market data and spot imbalances.
        market_data_dict: {'primary': df, 'context': df}
        context_extras: {'regime': str, 'sr_levels': dict}
        """
        # Check cost limit first
        if not self.state_manager.check_cost_limit():
            logger.warning("Daily API cost limit reached. Skipping analysis.")
            return None

        # Prepare context
        primary_df = market_data_dict['primary']
        context_df = market_data_dict.get('context')
        
        try:
            primary_csv = self._format_market_data_csv(primary_df)
            context_str = ""
            if context_df is not None:
                context_csv = self._format_market_data_csv(context_df)
                context_str = f"\\nHIGHER TIMEFRAME CONTEXT:\\n{context_csv}"
            
            extras_str = ""
            if context_extras:
                regime = context_extras.get('regime', 'UNKNOWN')
                sr = context_extras.get('sr_levels', {})
                # Format S/R levels
                supports = [str(l['price']) for l in sr.get('support', [])[:3]]
                resistances = [str(l['price']) for l in sr.get('resistance', [])[:3]]
                
                extras_str = f"\\nMARKET CONTEXT:\\nRegime: {regime}\\nKey Supports: {', '.join(supports)}\\nKey Resistances: {', '.join(resistances)}"
                
        except Exception as e:
            logger.error(f"Error preparing market data: {e}")
            return None
        
        user_message = f"Analyze this market data for {symbol} ({timeframe}):\\nPRIMARY TIMEFRAME:\\n{primary_csv}{context_str}{extras_str}"
        
        try:
            message = self.client.messages.create(
                model=Config.ANALYSIS_MODEL,
                max_tokens=1000,
                system=self._get_analysis_system_prompt(symbol, timeframe),
                messages=[{"role": "user", "content": user_message}]
            )
            
            self._track_cost(Config.ANALYSIS_MODEL, message.usage)
            
            response_text = message.content[0].text
            try:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start != -1 and end != -1:
                    json_str = response_text[start:end]
                    return json.loads(json_str)
                else:
                    logger.warning("No JSON found in LLM response")
                    return None
            except Exception as e:
                logger.error(f"Failed to parse Opus response JSON: {e}")
                return None
                
        except Exception as e:
            logger.error(f"LLM Analysis failed: {e}")
            return None
