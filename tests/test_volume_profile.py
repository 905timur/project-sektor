import sys
import os
import unittest
from unittest.mock import MagicMock
import pandas as pd
import numpy as np

# Mock dependencies to allow testing in incomplete environment
sys.modules['pandas_ta'] = MagicMock()
sys.modules['ccxt'] = MagicMock()
sys.modules['ccxt.pro'] = MagicMock()

# Mock Config
mock_config = MagicMock()
sys.modules['config'] = mock_config

# Mock ExchangeClient module
mock_exchange_module = MagicMock()
sys.modules['exchange_client'] = mock_exchange_module

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'files')))

from market_data import MarketDataManager

# Helper to calculate SMA
def calculate_sma(series, length):
    return series.rolling(window=length).mean()

# Helper to calculate ATR
def calculate_tr(df):
    df['h-l'] = df['high'] - df['low']
    df['h-pc'] = abs(df['high'] - df['close'].shift(1))
    df['l-pc'] = abs(df['low'] - df['close'].shift(1))
    return df[['h-l', 'h-pc', 'l-pc']].max(axis=1)

def calculate_atr(df, length=14):
    tr = calculate_tr(df)
    return tr.rolling(window=length).mean()

class MockExchange:
    pass

def test_volume_profile():
    print("Testing Volume Profile Calculation...")
    mgr = MarketDataManager(MockExchange())
    
    # Create synthetic data: 100 candles
    data = []
    for i in range(100):
        data.append({
            'timestamp': i * 60000,
            'open': 100 + (i % 10),
            'high': 105 + (i % 10),
            'low': 95 + (i % 10),
            'close': 100 + (i % 10),
            'volume': 1000 if i < 90 else 5000 
        })
        
    df = pd.DataFrame(data)
    
    vp = mgr.calculate_volume_profile(df, lookback=50)
    print(f"POC: {vp['poc']}, VAH: {vp['vah']}, VAL: {vp['val']}")
    
    if vp['poc'] > 0 and vp['vah'] > vp['val']:
        print("PASS: Volume Profile calculated.")
    else:
        print("FAIL: Volume Profile invalid.")

def test_order_block_volume_filter():
    print("\nTesting Order Block Volume Filter...")
    mgr = MarketDataManager(MockExchange())
    
    data = []
    # 50 context candles
    for i in range(50):
        data.append({
            'timestamp': i*60000, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 100
        })
        
    # Candle 50: The OB Candidate (Bearish) - Low Volume
    data.append({
        'timestamp': 50*60000, 'open': 100, 'high': 100, 'low': 90, 'close': 90, 'volume': 100
    })
    
    # Candle 51: Strong Move Up
    data.append({
        'timestamp': 51*60000, 'open': 90, 'high': 110, 'low': 90, 'close': 110, 'volume': 1000
    })
    
    df = pd.DataFrame(data)
    
    # Manual Indicator Calculation
    df['atr'] = calculate_atr(df)
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    
    # Backfill NaN
    df.bfill(inplace=True)
    df.fillna(0, inplace=True) # Ensure no NaNs
    
    # Test 1: Low Volume OB
    obs = mgr.detect_order_blocks(df, lookback=5)
    print(f"Low Volume OBs found: {len(obs)}")
    if len(obs) == 0:
        print("PASS: Low volume OB filtered.")
    else:
        print("FAIL: Low volume OB NOT filtered.")
        
    # Test 2: High Volume OB
    # Update Candle 50 volume
    # Note: modifying existing DF might not update derived columns if I don't recalc them
    # But here I modify source data then recalc
    
    df.iloc[50, df.columns.get_loc('volume')] = 500
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    df.fillna(0, inplace=True)
    
    obs = mgr.detect_order_blocks(df, lookback=5)
    print(f"High Volume OBs found: {len(obs)}")
    if len(obs) > 0:
        print("PASS: High volume OB detected.")
    else:
        print("FAIL: High volume OB NOT detected.")

def test_fvg_checks():
    print("\nTesting FVG Checks...")
    mgr = MarketDataManager(MockExchange())
    
    data = []
    for i in range(50):
         data.append({
            'timestamp': i*60000, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 100
        })
        
    # Candle 50: Base
    data.append({'timestamp': 50, 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 100})
    # Candle 51: Displacement (Low Volume)
    data.append({'timestamp': 51, 'open': 101, 'high': 110, 'low': 101, 'close': 109, 'volume': 100})
    # Candle 52: Gap validation
    data.append({'timestamp': 52, 'open': 109, 'high': 112, 'low': 105, 'close': 111, 'volume': 100})
    
    df = pd.DataFrame(data)
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    df = df.fillna(0)
    
    # Test 1: Low Volume FVG
    fvgs = mgr.detect_fair_value_gaps(df, lookback=5)
    print(f"Low Volume FVGs: {len(fvgs)}")
    if len(fvgs) == 0:
        print("PASS: Low volume FVG filtered.")
    else:
        print("FAIL: Low volume FVG NOT filtered.")
        
    # Test 2: High Volume FVG
    df.iloc[51, df.columns.get_loc('volume')] = 1000 
    df['vol_sma'] = calculate_sma(df['volume'], 20)
    df = df.fillna(0)
    
    fvgs = mgr.detect_fair_value_gaps(df, lookback=5)
    print(f"High Volume FVGs: {len(fvgs)}")
    if len(fvgs) > 0:
        print("PASS: High volume FVG detected.")
    else:
        print("FAIL: High volume FVG NOT detected.")

if __name__ == "__main__":
    test_volume_profile()
    test_order_block_volume_filter()
    test_fvg_checks()
