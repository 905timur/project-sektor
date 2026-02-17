import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sys
import os

# Add project root and files directory to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'files'))

# Mock pandas_ta before any imports that might use it
import sys
from unittest.mock import MagicMock
mock_ta = MagicMock()
sys.modules["pandas_ta"] = mock_ta

from strategy import ImbalanceStrategy
from opportunity_tracker import OpportunityTracker
from state_manager import StateManager

class TestStrategyFlow(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_exchange = MagicMock()
        self.mock_llm = MagicMock()
        self.mock_telegram = MagicMock()
        
        # Use real state manager but with a temp file (or mocked)
        self.state_manager = StateManager()
        self.state_manager.file_path = "test_state.json"
        self.state_manager.state = self.state_manager._get_default_state() # Reset state
        
        # Initialize strategy with mocks
        self.strategy = ImbalanceStrategy()
        self.strategy.exchange = self.mock_exchange
        self.strategy.llm = self.mock_llm
        self.strategy.telegram = self.mock_telegram
        self.strategy.state = self.state_manager
        self.strategy.tracker = OpportunityTracker(self.state_manager)
        self.strategy.paper_trading = MagicMock()
        self.strategy.paper_trading.get_available_balance.return_value = 1000.0
        self.mock_exchange.get_ticker.return_value = {'last': 100.0}
        
        # Mock config
        self.strategy.market_data.get_market_data = MagicMock()
        self.strategy.market_data.get_multi_timeframe_data = MagicMock()
        self.strategy.market_data.detect_fair_value_gaps = MagicMock()
        self.strategy.market_data.detect_order_blocks = MagicMock()
        self.strategy.market_data.detect_rejection_candle = MagicMock()
        
        # Mock database methods
        self.strategy.db.log_screening = MagicMock()
        self.strategy.db.update_screening_escalated = MagicMock()

    def tearDown(self):
        if os.path.exists("test_state.json"):
            os.remove("test_state.json")

    def test_end_to_end_flow(self):
        """
        Test the full flow:
        1. Scan finds FVG -> Adds to Watchlist
        2. Next cycle -> Price retraces -> Triggers Analysis
        3. Analysis returns BUY -> Trade Executed
        """
        symbol = "BTC/USDT"
        
        # --- PHASE 1: SCAN ---
        # Mock data for scan
        mock_df = pd.DataFrame({'close': [100, 105, 110], 'high': [100, 105, 110], 'low': [90, 95, 100]})
        self.strategy.market_data.get_market_data.return_value = mock_df
        
        # Mock FVG detection to return one FVG
        fvg = {
            'type': 'bullish',
            'top': 100,
            'bottom': 98,
            'index': 10,
            'timestamp': 1234567890,
            'created_at_price': 110
        }
        self.strategy.market_data.detect_fair_value_gaps.return_value = [fvg]
        self.strategy.market_data.detect_order_blocks.return_value = []
        
        # Run scan - only for one pair to avoid noise
        print("Running Scan...")
        with patch('config.Config.PAIRS', [symbol]):
            self.strategy.scan_market("daily")
        
        # Verify it was added to watchlist
        watchlist = self.strategy.tracker.get_watch_list()
        self.assertTrue(f"{symbol}_daily" in watchlist)
        self.assertEqual(watchlist[f"{symbol}_daily"]['stage'], 'watching')
        print("Phase 1 Passed: Opportunity added to watchlist.")

        # --- PHASE 2: RETRACEMENT ---
        # Mock data for retracement check
        # Price needs to be <= 100 (top of zone) and >= 98 (bottom)
        # We need at least 2 rows for detect_rejection_candle
        # Row 1 is a bullish hammer with high volume
        mock_df_retrace = pd.DataFrame({
            'open':    [100.0,  99.5],   # row 0 = prev, row 1 = current
            'high':    [101.0, 100.0],
            'low':     [ 99.0,  96.0],   # long lower wick on row 1
            'close':   [ 99.2,  99.4],   # close inside zone (98-100), bullish hammer
            'volume':  [1000.0, 1500.0], # row 1 volume 1.5x
            'vol_sma': [1000.0, 1000.0]  # vol_sma = 1000, so ratio = 1.5 > 1.3 ✓
        })
        
        self.strategy.market_data.get_multi_timeframe_data.return_value = {
            'primary': mock_df_retrace,
            'context': None
        }
        self.strategy.market_data.detect_rejection_candle.return_value = {"detected": True, "pattern": "hammer", "wick_ratio": 3.2}
        
        # Run pipeline (should hit check_watchlist_item)
        print("Running Pipeline for Retracement...")
        
        # Mock DeepSeek screening to approve
        self.mock_llm.screen_with_deepseek.return_value = {
            "signal": "BUY",
            "confidence": "HIGH",
            "reasoning": "Good setup detected",
            "proceed_to_full_analysis": True,
            "screening_id": "test_screening_123"
        }
        
        # Mock LLM analysis to approve
        self.mock_llm.analyze_opportunity.return_value = {
            "signal": "BUY",
            "confidence": "HIGH",
            "entry_target": 99,
            "stop_loss": 97,
            "take_profit": 105,
            "reasoning": "Great setup"
        }
        
        self.strategy.run_pipeline()
        
        # Verify DeepSeek screening was called
        self.mock_llm.screen_with_deepseek.assert_called()
        print("Phase 2a Passed: DeepSeek screening triggered.")
        
        # Verify LLM was called (after DeepSeek approved)
        self.mock_llm.analyze_opportunity.assert_called()
        print("Phase 2b Passed: Opus Analysis triggered after DeepSeek approval.")
        
        # Verify Trade Execution
        # check if state now has a position
        pos = self.strategy.state.get_position("daily")
        self.assertIsNotNone(pos)
        self.assertEqual(pos['symbol'], symbol)
        print("Phase 3 Passed: Trade Executed.")
        
        # Verify removed from watchlist
        watchlist_after = self.strategy.tracker.get_watch_list()
        self.assertFalse(f"{symbol}_daily" in watchlist_after)
        print("Phase 4 Passed: Removed from watchlist.")

if __name__ == '__main__':
    unittest.main()
