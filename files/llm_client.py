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

if TYPE_CHECKING:
    from news_client import RSSNewsClient

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self, state_manager, news_client: Optional['RSSNewsClient'] = None):
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        self.state_manager = state_manager
        self.news_client = news_client
        
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
        
        user_message = f"Analyze this market data for {symbol} ({timeframe}):\\nPRIMARY TIMEFRAME:\\n{primary_csv}{context_str}{extras_str}{news_section}"
        
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
                return {
                    "signal": "NEUTRAL",
                    "confidence": "LOW",
                    "reasoning": "Failed to parse DeepSeek response",
                    "proceed_to_full_analysis": True,  # Fallback to Opus
                    "screening_id": screening_id
                }
                
        except socket.timeout:
            logger.warning(f"⚠️ DeepSeek screening timed out after {Config.DEEPSEEK_TIMEOUT}s - falling back to Opus")
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
