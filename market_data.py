import pandas as pd
import pandas_ta as ta
from config import Config
from exchange_client import ExchangeClient
import logging

logger = logging.getLogger(__name__)

class MarketDataManager:
    def __init__(self, exchange_client):
        self.exchange = exchange_client

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
        """
        if df is None or len(df) < 3:
            return []
            
        fvgs = []
        # Iterate up to the second to last candle (current candle is still forming)
        for i in range(len(df) - lookback, len(df)):
            if i < 2: continue
            
            curr_low = df.iloc[i]['low']
            curr_high = df.iloc[i]['high']
            
            prev2_low = df.iloc[i-2]['low']
            prev2_high = df.iloc[i-2]['high']
            
            # Bullish FVG: Candle 1 High < Candle 3 Low (Gap Up)
            # (Note: i is Candle 3 in 0-indexed terms if we consider i-2, i-1, i)
            # Actually standard definition: Candle 1 High vs Candle 3 Low. 
            # If Candle 3 Low > Candle 1 High -> Gap.
            
            if df.iloc[i]['low'] > df.iloc[i-2]['high']:
                gap_size = (df.iloc[i]['low'] - df.iloc[i-2]['high']) / df.iloc[i-2]['high'] * 100
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
        Simplified: Last opposite candle before a significant move (>2x ATR).
        """
        if df is None or len(df) < 5:
            return []
            
        obs = []
        atr = df['atr'].iloc[-1]
        
        for i in range(len(df) - lookback, len(df) - 1):
            if i < 1: continue
            
            # Check for strong move after candle i
            move_up = (df.iloc[i+1]['close'] - df.iloc[i+1]['open']) > (2 * atr)
            move_down = (df.iloc[i+1]['open'] - df.iloc[i+1]['close']) > (2 * atr)
            
            # Bullish OB (Bearish candle 'i' followed by strong move up)
            if df.iloc[i]['close'] < df.iloc[i]['open'] and move_up:
                 obs.append({
                    'type': 'bullish',
                    'top': df.iloc[i]['high'],
                    'bottom': df.iloc[i]['low'],
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp']
                })
                
            # Bearish OB (Bullish candle 'i' followed by strong move down)
            elif df.iloc[i]['close'] > df.iloc[i]['open'] and move_down:
                obs.append({
                    'type': 'bearish',
                    'top': df.iloc[i]['high'],
                    'bottom': df.iloc[i]['low'],
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp']
                })
                
        return obs

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
