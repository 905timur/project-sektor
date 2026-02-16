import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        
        # Mock config
        self.strategy.market_data.get_market_data = MagicMock()
        self.strategy.market_data.get_multi_timeframe_data = MagicMock()
        self.strategy.market_data.detect_fair_value_gaps = MagicMock()
        self.strategy.market_data.detect_order_blocks = MagicMock()

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
        
        # Run scan
        print("Running Scan...")
        self.strategy.scan_market("daily")
        
        # Verify it was added to watchlist
        watchlist = self.strategy.tracker.get_watch_list()
        self.assertTrue(f"{symbol}_daily" in watchlist)
        self.assertEqual(watchlist[f"{symbol}_daily"]['stage'], 'watching')
        print("Phase 1 Passed: Opportunity added to watchlist.")

        # --- PHASE 2: RETRACEMENT ---
        # Mock data for retracement check
        # Price needs to be <= 100 (top of zone) and >= 98 (bottom)
        mock_df_retrace = pd.DataFrame({'close': [99], 'high': [99], 'low': [99]}) 
        self.strategy.market_data.get_multi_timeframe_data.return_value = {
            'primary': mock_df_retrace,
            'context': None
        }
        
        # Run pipeline (should hit check_watchlist_item)
        print("Running Pipeline for Retracement...")
        
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
        
        # Verify LLM was called
        self.mock_llm.analyze_opportunity.assert_called_once()
        print("Phase 2 Passed: LLM Analysis triggered.")
        
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
