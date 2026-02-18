import anthropic
import logging
import json
import os
import time
import socket
import urllib.request
import urllib.error
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Dict, Any, Optional, TYPE_CHECKING
from config import Config
from state_manager import StateManager
from belief_manager import BeliefManager

if TYPE_CHECKING:
    from news_client import RSSNewsClient

logger = logging.getLogger(__name__)
agent_logger = logging.getLogger('agent')

class LLMClient:
    def __init__(self, state_manager, news_client: Optional['RSSNewsClient'] = None):
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        self.state_manager = state_manager
        self.news_client = news_client
        
        # Belief manager for self-reflection
        self.belief_manager = BeliefManager()
        
        # Cached system prompts (built once, reused with caching)
        self._analysis_system_prompt = None
        self._approval_system_prompt = None

        # Setup agent logger
        self._setup_agent_logger()

    def _setup_agent_logger(self):
        """Setup separate logger for agent reasoning."""
        agent_logger.setLevel(logging.INFO)
        agent_handler = logging.FileHandler(Config.AGENT_LOG_FILE)
        agent_formatter = logging.Formatter('%(asctime)s - %(message)s')
        agent_handler.setFormatter(agent_formatter)
        agent_logger.addHandler(agent_handler)
        agent_logger.propagate = False  # Don't propagate to root logger

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

    def _fetch_news_with_timeout(self, symbol: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        """
        Fetch news with total timeout to avoid blocking trade execution.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            timeout: Total timeout in seconds (default: 15)
            
        Returns:
            News result dict or None if timeout/failure
        """
        if not self.news_client:
            return None
        
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                # Determine which news to fetch based on symbol
                if 'BTC' in symbol.upper():
                    future = executor.submit(self.news_client.get_bitcoin_news, 5)
                else:
                    future = executor.submit(self.news_client.get_all_news, 5)
                
                return future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning(f"News fetch timed out after {timeout}s, continuing without news")
            return None
        except Exception as e:
            logger.warning(f"News fetch failed: {e}, continuing without news")
            return None
    
    def _format_news_section(self, articles: list, sentiment: Dict[str, Any]) -> str:
        """
        Format news sentiment section for LLM prompt.
        
        Args:
            articles: List of article dictionaries
            sentiment: Sentiment analysis result
            
        Returns:
            Formatted string for LLM prompt
        """
        if not articles:
            return ""
        
        # Format headline section
        headlines = []
        for article in articles[:5]:  # Limit to 5 headlines
            title = article.get('title', 'No title')
            source = article.get('source', 'Unknown')
            # Truncate long titles
            if len(title) > 80:
                title = title[:77] + "..."
            headlines.append(f"- {title} ({source})")
        
        headlines_str = "\n".join(headlines)
        
        # Format sentiment summary
        overall = sentiment.get('overallSentiment', 'NEUTRAL')
        confidence = sentiment.get('confidence', 'LOW')
        bullish_pct = sentiment.get('bullishPercent', 0)
        bearish_pct = sentiment.get('bearishPercent', 0)
        
        return f"""

NEWS SENTIMENT (last {len(articles[:5])} articles):
Overall: {overall} ({confidence} confidence) | {bullish_pct}% bullish, {bearish_pct}% bearish
Headlines:
{headlines_str}"""

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
  "position_size_pct": float,  // 0.0 to 0.70 — recommended fraction of total balance to deploy
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
- Risk:Reward must be > 1.5 (Daily) or > 3.0 (Weekly)

**POSITION SIZING PHILOSOPHY:**

You are managing a live account. The current account balance in USDT will be provided in the user message.
Based on the balance and your confidence level, you must recommend a `position_size_pct` (0.0 to 0.70) in your JSON output.

Use the following scaling table:
- Balance < $200    → HIGH confidence: up to 0.70 | MEDIUM confidence: up to 0.40
- Balance $200–$500 → HIGH confidence: up to 0.50 | MEDIUM confidence: up to 0.30
- Balance $500–$1500→ HIGH confidence: up to 0.35 | MEDIUM confidence: up to 0.20
- Balance > $1500   → HIGH confidence: up to 0.25 | MEDIUM confidence: up to 0.15

Rules:
- Never exceed 0.70 regardless of confidence or balance.
- Always leave at least 10% of balance as a cash buffer — account for any existing open positions.
- If signal is NEUTRAL, set position_size_pct to 0.0.
- Be aggressive when the account is small; the goal is growth. Be conservative as capital grows; the goal becomes preservation.""",
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
        
        # Fetch news sentiment (non-blocking with 15s total timeout)
        news_section = ""
        if self.news_client:
            try:
                news_result = self._fetch_news_with_timeout(symbol, timeout=15)
                if news_result and news_result.get('articles'):
                    sentiment = self.news_client.analyze_sentiment(news_result['articles'])
                    news_section = self._format_news_section(news_result['articles'], sentiment)
                    logger.info(f"News sentiment: {sentiment.get('overallSentiment')} ({sentiment.get('confidence')} confidence)")
            except Exception as e:
                logger.warning(f"News fetch failed, continuing without: {e}")
        
        # Fetch account balance for position sizing
        # Try paper trading balance first, then fall back to capital state
        capital = self.state_manager.state.get("paper_trading", {}).get("balance", None)
        if capital is None or capital <= 0:
            capital = self.state_manager.state.get("capital", {}).get("current", 100.0)
        
        balance_line = f"CURRENT ACCOUNT BALANCE: ${capital:.2f} USDT\n\n"
        
        # --- Self-Reflection: Inject relevant beliefs ---
        beliefs_section = ""
        try:
            regime = None
            setup_type = None
            if context_extras:
                regime = context_extras.get('regime')
                setup_type = context_extras.get('rejection_pattern')  # FVG, OB, etc.
            
            beliefs_text = self.belief_manager.format_for_prompt(
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                setup_type=setup_type
            )
            if beliefs_text:
                beliefs_section = f"\n\nYOUR PAST TRADE REFLECTIONS (most relevant first):\n{beliefs_text}\n\nConsider these beliefs when evaluating this setup. They represent lessons from your own trading history."
        except Exception as e:
            logger.warning(f"Could not load beliefs: {e}")
        # --- End Self-Reflection ---
        
        user_message = f"{balance_line}{beliefs_section}Analyze this market data for {symbol} ({timeframe}):\\nPRIMARY TIMEFRAME:\\n{primary_csv}{context_str}{extras_str}{news_section}"
        
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
                    result = json.loads(json_str)
                    # Log agent reasoning for Opus analysis
                    signal = result.get('signal', 'NEUTRAL')
                    confidence = result.get('confidence', 'LOW')
                    reasoning = result.get('reasoning', 'No reasoning provided')
                    agent_logger.info(f"OPUS ANALYSIS - {symbol} ({timeframe}): {signal}/{confidence} - {reasoning}")
                    return result
                else:
                    logger.warning("No JSON found in LLM response")
                    return None
            except Exception as e:
                logger.error(f"Failed to parse Opus response JSON: {e}")
                return None
                
        except Exception as e:
            logger.error(f"LLM Analysis failed: {e}")
            return None
    
    def screen_with_deepseek(self, symbol, timeframe, market_data_dict, context_extras=None):
        """
        Uses DeepSeek R1 (free via OpenRouter) as a pre-screener before Opus.
        Returns a quick go/no-go decision to avoid unnecessary Opus calls.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            timeframe: 'daily' or 'weekly'
            market_data_dict: {'primary': df, 'context': df}
            context_extras: {'regime': str, 'sr_levels': dict}
            
        Returns:
            dict with signal, confidence, reasoning, proceed_to_full_analysis, screening_id
        """
        # Check if OpenRouter API key is configured
        if not Config.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY not set - skipping DeepSeek screening, proceeding to Opus")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": "OpenRouter API key not configured",
                "proceed_to_full_analysis": True,
                "screening_id": None
            }
        
        # Prepare market data
        primary_df = market_data_dict['primary']
        context_df = market_data_dict.get('context')
        
        try:
            primary_csv = self._format_market_data_csv(primary_df)
            context_str = ""
            if context_df is not None:
                context_csv = self._format_market_data_csv(context_df)
                context_str = f"\nHIGHER TIMEFRAME CONTEXT:\n{context_csv}"
            
            extras_str = ""
            if context_extras:
                regime = context_extras.get('regime', 'UNKNOWN')
                sr = context_extras.get('sr_levels', {})
                supports = [str(l['price']) for l in sr.get('support', [])[:3]]
                resistances = [str(l['price']) for l in sr.get('resistance', [])[:3]]
                extras_str = f"\nMARKET CONTEXT:\nRegime: {regime}\nKey Supports: {', '.join(supports)}\nKey Resistances: {', '.join(resistances)}"
                
        except Exception as e:
            logger.error(f"Error preparing market data for DeepSeek: {e}")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": f"Data preparation error: {str(e)}",
                "proceed_to_full_analysis": True,  # Fallback to Opus
                "screening_id": None
            }
        
        # Shortened system prompt for screening
        system_prompt = """You are a quick screener for cryptocurrency trade setups. Analyze the market data briefly and determine if this setup warrants full analysis.

Focus on:
1. Is there a clear imbalance zone (FVG or Order Block)?
2. Has price retraced into the zone?
3. Is the risk/reward favorable?

Return ONLY valid JSON:
{
  "signal": "BUY" | "SELL" | "NEUTRAL",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": "1-2 sentences max",
  "proceed_to_full_analysis": true | false
}

Note: A rejection candle pattern and volume spike (≥1.3x average) have already been confirmed on the most recent candle inside the imbalance zone before this screening request was triggered. Your job is to evaluate whether the broader market context, trend alignment, and risk/reward support taking this trade.

Rules:
- proceed_to_full_analysis = true ONLY when signal is BUY or SELL AND confidence is HIGH or MEDIUM
- If unclear or no clear setup, return NEUTRAL with proceed_to_full_analysis = false"""
        
        user_message = f"Screen this market data for {symbol} ({timeframe}):\nPRIMARY TIMEFRAME:\n{primary_csv}{context_str}{extras_str}"
        
        # Prepare request
        screening_id = f"screen_{int(time.time() * 1000)}"
        
        request_data = {
            "model": Config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }
        
        request_body = json.dumps(request_data).encode('utf-8')
        
        # Build request with required headers
        req = urllib.request.Request(
            f"{Config.OPENROUTER_BASE_URL}/chat/completions",
            data=request_body,
            headers={
                "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
                "HTTP-Referer": "imbalance-trading-bot",
                "X-Title": "Imbalance Bot",
                "Content-Type": "application/json"
            },
            method='POST'
        )
        
        try:
            # Set timeout and make request
            with socket.timeout(Config.DEEPSEEK_TIMEOUT):
                with urllib.request.urlopen(req) as response:
                    response_data = json.loads(response.read().decode('utf-8'))
            
            # Extract response content
            content = response_data.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            # Extract token usage (for logging only, no cost tracking - free tier)
            usage = response_data.get('usage', {})
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            logger.info(f"DeepSeek token usage: {prompt_tokens} prompt + {completion_tokens} completion = {prompt_tokens + completion_tokens} total")
            
            # DeepSeek R1 uses <think> blocks - strip them before JSON parsing
            # Find and remove <think>...</think> blocks
            think_end = content.find('</think>')
            if think_end != -1:
                content = content[think_end + 8:].strip()  # 8 is len('</think\>')
            
            # Parse JSON response (same approach as analyze_opportunity)
            start = content.find('{')
            end = content.rfind('}') + 1
            
            if start != -1 and end != -1:
                json_str = content[start:end]
                result = json.loads(json_str)
                
                # Ensure required fields exist
                signal = result.get('signal', 'NEUTRAL')
                confidence = result.get('confidence', 'LOW')
                reasoning = result.get('reasoning', 'No reasoning provided')
                
                # Calculate proceed_to_full_analysis based on rules
                # proceed_to_full_analysis = true ONLY when signal is BUY or SELL AND confidence is HIGH or MEDIUM
                proceed = signal in ['BUY', 'SELL'] and confidence in ['HIGH', 'MEDIUM']
                
                # Log agent reasoning for successful DeepSeek screening
                agent_logger.info(f"DEEPSEEK SCREENING - {symbol} ({timeframe}): {signal}/{confidence} - {reasoning}")
                return {
                    "signal": signal,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "proceed_to_full_analysis": proceed,
                    "screening_id": screening_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "raw_response": content
                }
            else:
                logger.warning(f"No JSON found in DeepSeek response")
                # Log agent reasoning for failed DeepSeek screening
                agent_logger.info(f"DEEPSEEK SCREENING - {symbol} ({timeframe}): NEUTRAL/LOW - Failed to parse DeepSeek response")
                return {
                    "signal": "NEUTRAL",
                    "confidence": "LOW",
                    "reasoning": "Failed to parse DeepSeek response",
                    "proceed_to_full_analysis": True,  # Fallback to Opus
                    "screening_id": screening_id
                }
                
        except socket.timeout:
            logger.warning(f"⚠️ DeepSeek screening timed out after {Config.DEEPSEEK_TIMEOUT}s - falling back to Opus")
            # Log agent reasoning for timeout
            agent_logger.info(f"DEEPSEEK SCREENING - {symbol} ({timeframe}): NEUTRAL/LOW - Timeout after {Config.DEEPSEEK_TIMEOUT}s")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": f"Timeout after {Config.DEEPSEEK_TIMEOUT}s",
                "proceed_to_full_analysis": True,  # Fallback to Opus
                "screening_id": screening_id
            }
        except urllib.error.URLError as e:
            logger.warning(f"⚠️ DeepSeek screening URL error: {e} - falling back to Opus")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": f"URL error: {str(e)}",
                "proceed_to_full_analysis": True,  # Fallback to Opus
                "screening_id": screening_id
            }
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ DeepSeek response JSON parse error: {e} - falling back to Opus")
            # Log agent reasoning for JSON parse error
            agent_logger.info(f"DEEPSEEK SCREENING - {symbol} ({timeframe}): NEUTRAL/LOW - JSON parse error: {str(e)}")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": f"JSON parse error: {str(e)}",
                "proceed_to_full_analysis": True,  # Fallback to Opus
                "screening_id": screening_id
            }
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek screening failed: {e} - falling back to Opus")
            return {
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reasoning": f"Error: {str(e)}",
                "proceed_to_full_analysis": True,  # Fallback to Opus
                "screening_id": screening_id
            }

    # =========================================================================
    # AI POSITION MANAGEMENT - DEEPSEEK & OPUS REVIEW
    # =========================================================================
    
    def review_position_deepseek(self, pos, timeframe, current_price, current_regime, current_sentiment, event_type):
        """
        Use DeepSeek (free via OpenRouter) for quick position review.
        Called when an event is detected that may require action.
        
        Args:
            pos: Position dict from state
            timeframe: "daily" or "weekly"
            current_price: Current market price
            current_regime: Current market regime
            current_sentiment: Current news sentiment
            event_type: The event that triggered this review
            
        Returns:
            dict with action, reasoning, urgency, suggested_stop_loss, raw_response
        """
        # Check if OpenRouter API key is configured
        if not Config.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY not set - skipping DeepSeek position review")
            return {
                "action": "HOLD",
                "reasoning": "OpenRouter API key not configured",
                "urgency": "LOW",
                "suggested_stop_loss": None
            }
        
        # Build compact position snapshot
        snapshot = {
            "symbol": pos["symbol"],
            "timeframe": timeframe,
            "side": "long",
            "trigger_event": event_type,
            "entry_price": pos["entry_price"],
            "current_price": current_price,
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "highest_price": pos.get("highest_price", pos["entry_price"]),
            "pnl_percent": round((current_price - pos["entry_price"]) / pos["entry_price"] * 100, 2),
            "duration_hours": round((time.time() - pos.get("timestamp", pos.get("opened_at", time.time()))) / 3600, 1),
            "regime_at_entry": pos.get("regime_at_entry", "UNKNOWN"),
            "regime_now": current_regime,
            "sentiment_at_entry": pos.get("news_sentiment_at_entry", "NEUTRAL"),
            "sentiment_now": current_sentiment
        }
        
        # System prompt for position review
        system_prompt = """You are a position management assistant for a crypto trading bot.
You will receive a snapshot of an open trade and the event that triggered this review.
Your job: decide if action is needed based on whether the original trade thesis is still intact.
Return ONLY valid JSON — no preamble, no markdown.

Output format:
{
  "action": "HOLD" | "TIGHTEN_STOP" | "EXIT_NOW" | "ESCALATE",
  "reasoning": "1-2 sentences",
  "urgency": "LOW" | "MEDIUM" | "HIGH",
  "suggested_stop_loss": float | null
}

Rules:
- HOLD: thesis intact, no action needed
- TIGHTEN_STOP: thesis weakening, reduce risk by moving SL closer, provide suggested_stop_loss
- EXIT_NOW: thesis broken, close the position immediately
- ESCALATE: situation is ambiguous or complex, needs Opus review
- Only ESCALATE when genuinely uncertain — not as a default
- suggested_stop_loss must be null unless action == TIGHTEN_STOP"""
        
        user_message = f"Position snapshot:\n{json.dumps(snapshot, indent=2)}"
        
        # Prepare request
        request_data = {
            "model": Config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }
        
        request_body = json.dumps(request_data).encode('utf-8')
        
        # Build request with required headers
        req = urllib.request.Request(
            f"{Config.OPENROUTER_BASE_URL}/chat/completions",
            data=request_body,
            headers={
                "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
                "HTTP-Referer": "imbalance-trading-bot",
                "X-Title": "Imbalance Bot",
                "Content-Type": "application/json"
            },
            method='POST'
        )
        
        try:
            # Set timeout and make request
            with socket.timeout(Config.DEEPSEEK_TIMEOUT):
                with urllib.request.urlopen(req) as response:
                    response_data = json.loads(response.read().decode('utf-8'))
            
            # Extract response content
            content = response_data.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            # Extract token usage (for logging only, no cost tracking - free tier)
            usage = response_data.get('usage', {})
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            logger.info(f"DeepSeek position review tokens: {prompt_tokens} prompt + {completion_tokens} completion = {prompt_tokens + completion_tokens} total")
            
            # DeepSeek R1 uses <think\> blocks - strip them before JSON parsing
            think_end = content.find(r'</think\>')
            if think_end != -1:
                content = content[think_end + 8:].strip()
            
            # Parse JSON response
            start = content.find('{')
            end = content.rfind('}') + 1
            
            if start != -1 and end != -1:
                json_str = content[start:end]
                result = json.loads(json_str)
                
                action = result.get('action', 'HOLD')
                reasoning = result.get('reasoning', 'No reasoning provided')
                urgency = result.get('urgency', 'LOW')
                suggested_sl = result.get('suggested_stop_loss')
                
                # Log agent reasoning
                agent_logger.info(f"DEEPSEEK POSITION REVIEW - {pos['symbol']} ({event_type}): {action} / {urgency} - {reasoning}")
                
                return {
                    "action": action,
                    "reasoning": reasoning,
                    "urgency": urgency,
                    "suggested_stop_loss": suggested_sl,
                    "raw_response": content
                }
            else:
                logger.warning(f"No JSON found in DeepSeek position review response")
                agent_logger.info(f"DEEPSEEK POSITION REVIEW - {pos['symbol']} ({event_type}): HOLD / LOW - Failed to parse response")
                return {
                    "action": "HOLD",
                    "reasoning": "Failed to parse DeepSeek response",
                    "urgency": "LOW",
                    "suggested_stop_loss": None
                }
                
        except socket.timeout:
            logger.warning(f"⚠️ DeepSeek position review timed out after {Config.DEEPSEEK_TIMEOUT}s")
            agent_logger.info(f"DEEPSEEK POSITION REVIEW - {pos['symbol']} ({event_type}): HOLD / LOW - Timeout")
            return {
                "action": "HOLD",
                "reasoning": "DeepSeek timed out — holding",
                "urgency": "LOW",
                "suggested_stop_loss": None
            }
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek position review failed: {e}")
            agent_logger.info(f"DEEPSEEK POSITION REVIEW - {pos['symbol']} ({event_type}): HOLD / LOW - Error: {str(e)}")
            return {
                "action": "HOLD",
                "reasoning": f"DeepSeek error: {str(e)} — holding",
                "urgency": "LOW",
                "suggested_stop_loss": None
            }
    
    def review_position_opus(self, pos, timeframe, current_price, current_regime, current_sentiment, event_type, recent_candles_df):
        """
        Use Opus for detailed position review when DeepSeek escalates.
        
        Args:
            pos: Position dict from state
            timeframe: "daily" or "weekly"
            current_price: Current market price
            current_regime: Current market regime
            current_sentiment: Current news sentiment
            event_type: The event that triggered this review
            recent_candles_df: DataFrame with recent candles for context
            
        Returns:
            dict with action, reasoning, new_stop_loss, confidence, thesis_intact
        """
        # Build compact position snapshot
        snapshot = {
            "symbol": pos["symbol"],
            "timeframe": timeframe,
            "side": "long",
            "trigger_event": event_type,
            "entry_price": pos["entry_price"],
            "current_price": current_price,
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "highest_price": pos.get("highest_price", pos["entry_price"]),
            "pnl_percent": round((current_price - pos["entry_price"]) / pos["entry_price"] * 100, 2),
            "duration_hours": round((time.time() - pos.get("timestamp", pos.get("opened_at", time.time()))) / 3600, 1),
            "regime_at_entry": pos.get("regime_at_entry", "UNKNOWN"),
            "regime_now": current_regime,
            "sentiment_at_entry": pos.get("news_sentiment_at_entry", "NEUTRAL"),
            "sentiment_now": current_sentiment
        }
        
        # Format recent candles (last 5 only)
        candles_csv = self._format_market_data_csv(recent_candles_df.tail(5))
        
        # System prompt for Opus position review (cached)
        system_prompt = [
            {
                "type": "text",
                "text": """You are an expert imbalance trader reviewing an open position.

Your original entry was based on an imbalance setup (FVG or Order Block). 
Your job is to assess whether that thesis remains valid given current conditions.

You will receive:
1. A position snapshot with entry details and current P&L
2. The last 5 candles of price action on the entry timeframe (CSV: open,high,low,close,volume)
3. The event that triggered this review

Assess:
- Is price respecting the original imbalance zone or has it broken structure?
- Has the market regime or momentum shifted against the trade?
- Is the current stop loss placement still logical, or should it be adjusted?
- Does the risk/reward still justify holding?

Return ONLY valid JSON:
{
  "action": "HOLD" | "EXIT_NOW" | "EXIT_PARTIAL" | "ADJUST_STOP",
  "reasoning": "2-3 sentence explanation of thesis status",
  "new_stop_loss": float | null,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "thesis_intact": true | false
}

Rules:
- EXIT_PARTIAL means close 50% of position size (only recommend if partial exits are meaningful)
- Only adjust stop if you have a specific structural level to reference
- Be decisive — HOLD means you genuinely believe the setup is still valid
- new_stop_loss must be null unless action is ADJUST_STOP""",
                "cache_control": {"type": "ephemeral"}
            }
        ]
        
        user_message = f"Position snapshot:\n{json.dumps(snapshot, indent=2)}\n\nLast 5 candles (open,high,low,close,volume):\n{candles_csv}"
        
        try:
            # Call Opus with cached system prompt
            response = self.client.messages.create(
                model=Config.ANALYSIS_MODEL,
                max_tokens=400,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ]
            )
            
            # Track cost
            self._track_cost(Config.ANALYSIS_MODEL, response.usage)
            
            # Parse response
            content = response.content[0].text
            
            # Parse JSON
            start = content.find('{')
            end = content.rfind('}') + 1
            
            if start != -1 and end != -1:
                json_str = content[start:end]
                result = json.loads(json_str)
                
                action = result.get('action', 'HOLD')
                reasoning = result.get('reasoning', 'No reasoning provided')
                new_sl = result.get('new_stop_loss')
                confidence = result.get('confidence', 'LOW')
                thesis_intact = result.get('thesis_intact', True)
                
                # Log agent reasoning
                agent_logger.info(f"OPUS POSITION REVIEW - {pos['symbol']} ({event_type}): {action} / thesis_intact={thesis_intact} - {reasoning}")
                
                return {
                    "action": action,
                    "reasoning": reasoning,
                    "new_stop_loss": new_sl,
                    "confidence": confidence,
                    "thesis_intact": thesis_intact
                }
            else:
                logger.warning(f"No JSON found in Opus position review response")
                agent_logger.info(f"OPUS POSITION REVIEW - {pos['symbol']} ({event_type}): HOLD - Failed to parse response")
                return {
                    "action": "HOLD",
                    "reasoning": "Failed to parse Opus response",
                    "new_stop_loss": None,
                    "confidence": "LOW",
                    "thesis_intact": True
                }
                
        except Exception as e:
            logger.error(f"Opus position review failed: {e}")
            agent_logger.info(f"OPUS POSITION REVIEW - {pos['symbol']} ({event_type}): HOLD - Error: {str(e)}")
            return {
                "action": "HOLD",
                "reasoning": "Opus review failed — holding",
                "new_stop_loss": None,
                "confidence": "LOW",
                "thesis_intact": True
            }
    
    def generate_belief_update(self, trade_record: dict) -> dict | None:
        """
        Given a fully closed trade record, ask Opus to write one belief update
        in its own voice reflecting on what it learned from this trade.
        
        Args:
            trade_record: dict with keys: symbol, timeframe, side, entry_price, 
                         exit_price, pnl, pnl_percent, exit_reason, regime, 
                         reason, analysis_context
        
        Returns:
            dict ready for BeliefManager.add_belief() or None on failure
        """
        # Extract fields from trade_record with graceful defaults
        symbol = trade_record.get("symbol", "UNKNOWN")
        timeframe = trade_record.get("timeframe", "unknown")
        side = trade_record.get("side", "buy")
        entry_price = trade_record.get("entry_price", 0)
        exit_price = trade_record.get("exit_price", 0)
        pnl = trade_record.get("pnl", 0)
        pnl_percent = trade_record.get("pnl_percent", 0)
        exit_reason = trade_record.get("exit_reason", "Unknown")
        regime = trade_record.get("regime", trade_record.get("regime_at_entry", "UNKNOWN"))
        entry_reason = trade_record.get("reason", "")
        analysis_context = trade_record.get("analysis_context", {})
        
        # Determine outcome string
        if pnl > 0:
            outcome = "WIN"
        elif pnl < 0:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"
        
        # Build user message with trade details
        opus_reasoning = analysis_context.get("reasoning", "No analysis context available") if analysis_context else "No analysis context available"
        
        user_message = f"""Closed trade record:
Symbol: {symbol} ({timeframe})
Side: {side}
Result: {outcome} | ${pnl:+.2f} ({pnl_percent:+.2f}%)
Entry Reason: {entry_reason}
Exit Reason: {exit_reason}
Regime at Entry: {regime}
Opus Reasoning at Entry: {opus_reasoning}

Write a belief update. What did this trade confirm or challenge about your approach?
What specific condition will you look for differently next time?"""
        
        # System prompt for belief generation
        system_prompt = """You are a self-reflective trading agent reviewing your own closed trade.
Write a single, specific belief update in first-person based on what this trade taught you.
Be concrete — reference the specific symbol, regime, pattern, and what you would do differently (or the same) next time.
Avoid generic statements like 'I should be more careful'. Instead say exactly what condition to look for or avoid.
Return ONLY valid JSON.

Expected JSON format:
{
  "belief": "First-person reflection string (2-4 sentences, specific and actionable)",
  "tags": ["list", "of", "1-4", "short", "keyword", "tags"],
  "confidence_in_belief": "HIGH" | "MEDIUM" | "LOW"
}"""
        
        try:
            message = self.client.messages.create(
                model=Config.ANALYSIS_MODEL,
                max_tokens=300,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )
            
            self._track_cost(Config.ANALYSIS_MODEL, message.usage)
            
            response_text = message.content[0].text
            
            # Parse JSON response
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            
            if start != -1 and end != -1:
                json_str = response_text[start:end]
                result = json.loads(json_str)
                
                belief_text = result.get("belief", "")
                tags = result.get("tags", [])
                confidence = result.get("confidence_in_belief", "MEDIUM")
                
                # Build complete belief dict
                belief_dict = {
                    "id": f"belief_{int(time.time())}",
                    "timestamp": time.time(),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "outcome": outcome,
                    "pnl_percent": round(pnl_percent, 2),
                    "belief": belief_text,
                    "tags": tags,
                    "confidence_in_belief": confidence
                }
                
                # Log to agent logger
                agent_logger.info(f"BELIEF GENERATED - {symbol} ({outcome}): {belief_text[:100]}...")
                
                return belief_dict
            else:
                logger.warning(f"No JSON found in belief generation response for {symbol}")
                return None
                
        except Exception as e:
            logger.warning(f"Belief generation failed for {symbol}: {e}")
            return None
