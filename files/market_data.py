import pandas as pd
import pandas_ta as ta
from config import Config
from exchange_client import ExchangeClient
import logging

logger = logging.getLogger(__name__)

class MarketDataManager:
    def __init__(self, exchange_client):
        self.exchange = exchange_client

    def calculate_volume_profile(self, df, lookback=100, bins=50):
        """
        Calculates the Volume Profile for the last `lookback` candles.
        Returns a dict: {'vah': float, 'val': float, 'poc': float, 'profile': pd.DataFrame}
        """
        if df is None or len(df) < lookback:
            return None
        
        # Use only the lookback period
        subset = df.iloc[-lookback:].copy()
        
        # Define price bins
        price_min = subset['low'].min()
        price_max = subset['high'].max()
        price_range = price_max - price_min
        
        if price_range == 0:
            return None

        # Create bins
        bin_size = price_range / bins
        subset['price_bin'] = pd.cut(subset['close'], bins=bins, retbins=False)
        
        # Group by bin and sum volume
        profile = subset.groupby('price_bin')['volume'].sum().reset_index()
        
        # Find Point of Control (POC) - Price bin with max volume
        max_vol_idx = profile['volume'].idxmax()
        poc_bin = profile.iloc[max_vol_idx]['price_bin']
        poc = poc_bin.mid
        
        # Calculate Value Area (70% of volume)
        total_volume = profile['volume'].sum()
        value_area_vol = total_volume * 0.70
        
        # Sort by volume descending to find the bins that make up 70%
        profile_sorted = profile.sort_values(by='volume', ascending=False)
        profile_sorted['cum_vol'] = profile_sorted['volume'].cumsum()
        
        # Get bins inside the Value Area
        va_bins = profile_sorted[profile_sorted['cum_vol'] <= value_area_vol]
        
        # If no bins found (e.g. one bin has > 70%), take the top one
        if va_bins.empty:
            va_bins = profile_sorted.iloc[[0]]
            
        # Get VAH and VAL from the intervals in the Value Area
        # We need to extract .left and .right from the Interval objects
        va_intervals = va_bins['price_bin'].values
        val = min([i.left for i in va_intervals])
        vah = max([i.right for i in va_intervals])
        
        return {
            'poc': poc,
            'vah': vah,
            'val': val,
            'profile': profile
        }

    def get_market_data(self, symbol, timeframe):
        """
        Fetches OHLCV and calculates indicators.
        Returns a DataFrame with indicators.
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=200)
            if not ohlcv:
                return None
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # --- Technical Indicators ---
            
            # RSI
            df['rsi'] = df.ta.rsi(length=14)
            
            # Bollinger Bands
            bbands = df.ta.bbands(length=20, std=2)
            if bbands is not None:
                df = pd.concat([df, bbands], axis=1)

            # MACD
            macd = df.ta.macd(fast=12, slow=26, signal=9)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
                
            # ATR (for stop loss)
            df['atr'] = df.ta.atr(length=14)

            # Volume SMA (to spot volume spikes)
            df['vol_sma'] = df.ta.sma(close='volume', length=20)
            
            # ADX for Trend Strength
            adx = df.ta.adx(length=14)
            if adx is not None:
                df = pd.concat([df, adx], axis=1)

            # Imbalance / Extension calculation (Simple z-score like extension from EMA)
            df['ema_50'] = df.ta.ema(length=50)
            df['extension'] = (df['close'] - df['ema_50']) / df['ema_50'] * 100

            return df
        except Exception as e:
            logger.error(f"Error processing market data for {symbol}: {e}")
            return None

    def get_multi_timeframe_data(self, symbol, primary_tf_name):
        """
        Fetches data for both the primary timeframe and its lower-timeframe context.
        Returns a dict: {'primary': df, 'context': df}
        """
        # 1. Get Primary Data (e.g., Daily)
        primary_code = Config.TIMEFRAMES.get(primary_tf_name)
        if not primary_code:
            logger.error(f"Invalid primary timeframe name: {primary_tf_name}")
            return None
            
        primary_df = self.get_market_data(symbol, primary_code)
        if primary_df is None:
            return None
            
        # 2. Get Context Data (e.g., 4H for Daily)
        context_code = Config.MULTI_TIMEFRAME_MAPPING.get(primary_tf_name)
        if not context_code:
            # Fallback or error? Let's just return primary if no mapping
            return {'primary': primary_df, 'context': None}
            
        context_df = self.get_market_data(symbol, context_code)
        
        return {
            'primary': primary_df,
            'context': context_df
        }

    def detect_fair_value_gaps(self, df, lookback=20):
        """
        Detect unfilled fair value gaps in recent candles.
        Returns a list of dicts: {'type', 'top', 'bottom', 'index', 'filled'}
        Enhanced: Checks for displacement volume and Value Area context.
        """
        if df is None or len(df) < 50: # Need history for VP
            return []
            
        fvgs = []
        
        # Calculate Volume Profile
        vp = self.calculate_volume_profile(df, lookback=50)
        vah, val = (vp['vah'], vp['val']) if vp else (None, None)
        
        # Iterate up to the second to last candle (current candle is still forming)
        for i in range(len(df) - lookback, len(df)):
            if i < 2: continue
            
            # Check displacement candle (i-1) volume
            # We want the move that created the gap to have volume
            disp_vol = df.iloc[i-1]['volume']
            disp_vol_sma = df.iloc[i-1]['vol_sma']
            
            # If volume is weak, skip (unless it's massive gap? let's filter strict for now)
            # Using 1.5x SMA for FVG displacement as a baseline
            if pd.notna(disp_vol_sma) and disp_vol < (1.5 * disp_vol_sma):
                continue

            curr_low = df.iloc[i]['low']
            curr_high = df.iloc[i]['high']
            
            prev2_low = df.iloc[i-2]['low']
            prev2_high = df.iloc[i-2]['high']
            
            # Bullish FVG: Candle 1 High < Candle 3 Low (Gap Up)
            if df.iloc[i]['low'] > df.iloc[i-2]['high']:
                gap_size = (df.iloc[i]['low'] - df.iloc[i-2]['high']) / df.iloc[i-2]['high'] * 100
                
                # Check VA overlap (Support)
                # If gap is completely above VAH, it's a breakout (good).
                # If gap is inside VA, it's trading within value.
                # If gap is below VAL, it's bearish territory? (For bullish FVG, being below VAL is Reversal?)
                # User said "confirm if the imbalance zones have high-volume nodes"
                # We'll allow if it overlaps VA OR is a breakout from VA.
                # Strictly filtering logic: "filters out weak setups where where volume doesn't support"
                
                if gap_size > 0.1: # Min 0.1% gap
                    fvgs.append({
                        'type': 'bullish',
                        'top': df.iloc[i]['low'],
                        'bottom': df.iloc[i-2]['high'],
                        'index': i,
                        'timestamp': df.iloc[i]['timestamp'],
                        'created_at_price': df.iloc[i]['close']
                    })
                    
            # Bearish FVG: Candle 1 Low > Candle 3 High (Gap Down)
            elif df.iloc[i]['high'] < df.iloc[i-2]['low']:
                gap_size = (df.iloc[i-2]['low'] - df.iloc[i]['high']) / df.iloc[i]['high'] * 100
                if gap_size > 0.1:
                    fvgs.append({
                        'type': 'bearish',
                        'top': df.iloc[i-2]['low'],
                        'bottom': df.iloc[i]['high'],
                        'index': i,
                        'timestamp': df.iloc[i]['timestamp'],
                        'created_at_price': df.iloc[i]['close']
                    })
                    
        return fvgs

    def detect_order_blocks(self, df, lookback=50):
        """
        Detect potential order blocks.
        Bullish OB: Last bearish candle before a strong move up (break of structure).
        Bearish OB: Last bullish candle before a strong move down.
        Enhanced: 
        1. Volume of the OB candle > 2 * SMA(20)
        2. Optional: OB price should strictly interact with Value Area (e.g. simple check for now: overlap)
        """
        if df is None or len(df) < 22: # Need at least 20 for SMA + 2 for calculation
            return []
            
        obs = []
        atr = df['atr'].iloc[-1]
        
        # Calculate Volume Profile once for the recent window to use for filtering
        # We'll use a lookback of 50 for the profile context
        vp = self.calculate_volume_profile(df, lookback=50)
        vah, val = (vp['vah'], vp['val']) if vp else (None, None)
        
        for i in range(len(df) - lookback, len(df) - 1):
            if i < 20: continue # Need history for vol_sma
            
            # 1. Volume Check
            # Ensure the candle creating the OB has significant volume
            ob_vol = df.iloc[i]['volume']
            vol_sma = df.iloc[i]['vol_sma']
            
            # Condition 1: High Volume on the formation candle (Institutions loading up)
            if ob_vol <= (2.0 * vol_sma):
                continue
            
            # Check for strong move after candle i
            move_up = (df.iloc[i+1]['close'] - df.iloc[i+1]['open']) > (2 * atr)
            move_down = (df.iloc[i+1]['open'] - df.iloc[i+1]['close']) > (2 * atr)
            
            # Bullish OB (Bearish candle 'i' followed by strong move up)
            if df.iloc[i]['close'] < df.iloc[i]['open'] and move_up:
                 # Value Area Check: Ideally, we want to see if the OB is in a "value" zone or edge
                 # For now, let's just log if it's within/near VA. 
                 # If we return it, it's a valid candidate.
                 
                 obs.append({
                    'type': 'bullish',
                    'top': df.iloc[i]['high'],
                    'bottom': df.iloc[i]['low'],
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'strength': 'high' if (val and df.iloc[i]['low'] <= val) else 'normal'
                })
                
            # Bearish OB (Bullish candle 'i' followed by strong move down)
            elif df.iloc[i]['close'] > df.iloc[i]['open'] and move_down:
                obs.append({
                    'type': 'bearish',
                    'top': df.iloc[i]['high'],
                    'bottom': df.iloc[i]['low'],
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'strength': 'high' if (vah and df.iloc[i]['high'] >= vah) else 'normal'
                })
                
        return obs

    def detect_rejection_candle(self, df, bias: str) -> dict:
        """
        Inspects the most recent completed candle for rejection patterns
        appropriate to the given bias direction.

        For BULLISH bias (price dropping into a support/FVG zone), valid patterns:
          - Hammer / Pin Bar:     lower wick >= 2x candle body, small upper wick
          - Bullish Engulfing:    current close > prev open AND current open < prev close
                                  (current body fully engulfs previous bearish body)
          - Dragonfly Doji:       open ≈ close (body < 0.3% of candle range),
                                  long lower wick

        For BEARISH bias (price rising into a resistance/FVG zone), valid patterns:
          - Shooting Star:        upper wick >= 2x candle body, small lower wick
          - Bearish Engulfing:    current close < prev open AND current open > prev close
          - Gravestone Doji:      open ≈ close (body < 0.3% of candle range),
                                  long upper wick

        Returns a dict:
        {
          "detected": True | False,
          "pattern":  str  (e.g. "hammer", "bullish_engulfing", "shooting_star", etc.)
                           or "none" if not detected,
          "wick_ratio": float  (wick / body ratio, useful for logging)
        }
        """
        if df is None or len(df) < 2:
            return {"detected": False, "pattern": "none", "wick_ratio": 0.0}

        candle  = df.iloc[-1]   # most recent candle
        prev    = df.iloc[-2]   # previous candle

        o = candle['open']
        h = candle['high']
        l = candle['low']
        c = candle['close']

        body        = abs(c - o)
        candle_range = h - l
        if candle_range == 0:
            return {"detected": False, "pattern": "none", "wick_ratio": 0.0}

        upper_wick  = h - max(o, c)
        lower_wick  = min(o, c) - l
        body_pct    = body / candle_range  # body as fraction of total range

        # --- BULLISH PATTERNS ---
        if bias == 'bullish':

            # Hammer / Pin Bar
            # Criteria: lower wick >= 2x body, upper wick <= 0.3x body, body exists
            if body > 0 and lower_wick >= 2.0 * body and upper_wick <= 0.3 * body:
                return {
                    "detected": True,
                    "pattern": "hammer",
                    "wick_ratio": round(lower_wick / body, 2)
                }

            # Dragonfly Doji
            # Criteria: body < 30% of range, lower wick > 60% of range
            if body_pct < 0.30 and (lower_wick / candle_range) > 0.60:
                return {
                    "detected": True,
                    "pattern": "dragonfly_doji",
                    "wick_ratio": round(lower_wick / candle_range, 2)
                }

            # Bullish Engulfing
            # Criteria: prev candle bearish, current candle bullish and body engulfs prev body
            prev_bearish  = prev['close'] < prev['open']
            curr_bullish  = c > o
            engulfs       = o <= prev['close'] and c >= prev['open']
            if prev_bearish and curr_bullish and engulfs:
                return {
                    "detected": True,
                    "pattern": "bullish_engulfing",
                    "wick_ratio": round(body / candle_range, 2)
                }

        # --- BEARISH PATTERNS ---
        elif bias == 'bearish':

            # Shooting Star
            # Criteria: upper wick >= 2x body, lower wick <= 0.3x body, body exists
            if body > 0 and upper_wick >= 2.0 * body and lower_wick <= 0.3 * body:
                return {
                    "detected": True,
                    "pattern": "shooting_star",
                    "wick_ratio": round(upper_wick / body, 2)
                }

            # Gravestone Doji
            # Criteria: body < 30% of range, upper wick > 60% of range
            if body_pct < 0.30 and (upper_wick / candle_range) > 0.60:
                return {
                    "detected": True,
                    "pattern": "gravestone_doji",
                    "wick_ratio": round(upper_wick / candle_range, 2)
                }

            # Bearish Engulfing
            # Criteria: prev candle bullish, current candle bearish and body engulfs prev body
            prev_bullish  = prev['close'] > prev['open']
            curr_bearish  = c < o
            engulfs       = o >= prev['close'] and c <= prev['open']
            if prev_bullish and curr_bearish and engulfs:
                return {
                    "detected": True,
                    "pattern": "bearish_engulfing",
                    "wick_ratio": round(body / candle_range, 2)
                }

        return {"detected": False, "pattern": "none", "wick_ratio": 0.0}

    def detect_market_regime(self, df):
        """
        Classify market as TRENDING_UP, TRENDING_DOWN, RANGING, or VOLATILE.
        """
        if df is None or len(df) < 50:
            return 'RANGING'
        
        last_row = df.iloc[-1]
        
        # 1. Volatility Check (High relative ATR)
        current_atr = last_row['atr']
        avg_atr = df['atr'].rolling(20).mean().iloc[-1]
        
        if current_atr > (avg_atr * 1.5):
            return 'VOLATILE'
            
        # 2. Trend Strength Check (ADX)
        # pandas-ta ADX returns multiple columns sometimes, usually ADX_14
        adx_col = [c for c in df.columns if 'ADX' in c]
        if not adx_col:
            return 'RANGING'
        
        current_adx = last_row[adx_col[0]]
        
        if current_adx > 25:
            # Strong Trend
            if last_row['close'] > last_row['ema_50']:
                return 'TRENDING_UP'
            else:
                return 'TRENDING_DOWN'
                
        return 'RANGING'

    def identify_support_resistance(self, df, lookback=50):
        """
        Identify key support and resistance levels based on pivot highs/lows.
        Returns {'support': [], 'resistance': []}
        """
        if df is None or len(df) < lookback:
            return {'support': [], 'resistance': []}
            
        levels = {'support': [], 'resistance': []}
        
        # Simple pivot detection: High > surrounding 5 candles
        window = 5
        
        for i in range(len(df) - lookback, len(df) - window):
            if i < window: continue
            
            # Check for Pivot High
            is_pivot_high = True
            for j in range(1, window + 1):
                if df.iloc[i]['high'] < df.iloc[i-j]['high'] or df.iloc[i]['high'] < df.iloc[i+j]['high']:
                    is_pivot_high = False
                    break
            
            if is_pivot_high:
                levels['resistance'].append({
                    'price': df.iloc[i]['high'],
                    'index': i,
                    'age': len(df) - i
                })
                
            # Check for Pivot Low
            is_pivot_low = True
            for j in range(1, window + 1):
                if df.iloc[i]['low'] > df.iloc[i-j]['low'] or df.iloc[i]['low'] > df.iloc[i+j]['low']:
                    is_pivot_low = False
                    break
            
            if is_pivot_low:
                levels['support'].append({
                    'price': df.iloc[i]['low'],
                    'index': i,
                    'age': len(df) - i
                })
                
        # Sort by recency (age)
        levels['support'].sort(key=lambda x: x['age'])
        levels['resistance'].sort(key=lambda x: x['age'])
        
        return levels

    def check_imbalance(self, df):
        """
        Enhanced filter: Checks for FVG, Order Blocks, AND technical extremes.
        Passes if ANY significant structure is found.
        """
        if df is None or df.empty:
            return False
            
        # 1. Check for recent FVGs (last 5 candles)
        recent_fvgs = self.detect_fair_value_gaps(df, lookback=5)
        if len(recent_fvgs) > 0:
            return True
            
        # 2. Check for recent Order Blocks (last 5 candles)
        recent_obs = self.detect_order_blocks(df, lookback=5)
        if len(recent_obs) > 0:
            return True

        # 3. Fallback to original extreme checks (for safety/catching major moves)
        last_row = df.iloc[-1]
        
        # RSI Extremes
        rsi_oversold = last_row['rsi'] < 30
        rsi_overbought = last_row['rsi'] > 70
        
        # Extension
        extended = abs(last_row['extension']) > 5.0
        
        # Volume Spike
        vol_spike = last_row['volume'] > (last_row['vol_sma'] * 3.0) # Increased threshold
        
        if (rsi_oversold or rsi_overbought) and (vol_spike or extended):
            return True
            
        return False
