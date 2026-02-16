import unittest
import sys
import os
import shutil
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../files")))

from paper_trading import PaperTradingManager
from config import Config

# Mock StateManager
class MockStateManager:
    def __init__(self):
        self.state = {}
    
    def save_state(self):
        pass

class TestPaperTradingCosts(unittest.TestCase):
    def setUp(self):
        self.manager = PaperTradingManager(MockStateManager())
        # Reset balance to known state
        self.manager.state.state["paper_trading"] = {
            "initial_balance": 10000.0,
            "balance": 10000.0,
            "available_balance": 10000.0,
            "total_profit": 0.0,
            "total_loss": 0.0,
            "realized_pnl": 0.0,
            "trades_executed": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "positions": {
                "daily": None,
                "weekly": None
            },
            "trade_history": [],
            "started_at": datetime.now().isoformat()
        }

    def test_buy_slippage_and_fees(self):
        """Test that BUY orders incur slippage and fees."""
        # Using self.manager from setUp
        initial_balance = self.manager.get_available_balance()
        quantity = 0.1
        price = 50000.0
        
        # Place Order
        result = self.manager.place_order("BTC/USDT", "buy", quantity, price, "daily")
        
        self.assertTrue(result["success"])
        
        # Check that execution price includes slippage
        execution_price = result["price"]
        self.assertGreater(execution_price, price)
        self.assertLessEqual(execution_price, price * (1 + Config.PAPER_SLIPPAGE_RATE_MAX))
        self.assertGreaterEqual(execution_price, price * (1 + Config.PAPER_SLIPPAGE_RATE_MIN))
        
        # Check that cost includes fees
        transaction_value = quantity * execution_price
        fee = result["fee"]
        self.assertAlmostEqual(fee, transaction_value * Config.PAPER_FEE_RATE, places=2)
        
        # Check balance deduction
        new_balance = self.manager.get_available_balance()
        cost = transaction_value + fee
        self.assertAlmostEqual(new_balance, initial_balance - cost, places=2)

    def test_sell_slippage_and_fees(self):
        """Test that SELL orders allow for slippage and fees."""
        
        # Setup an existing position
        self.manager.state.state["paper_trading"]["positions"]["daily"] = {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "fee_paid": 20.0,
            "slippage_rate": 0.002,
            "cost": 5020.0, # 5000 + 20 fee
            "opened_at": 1234567890,
            "timeframe": "daily",
            "highest_price": 50000.0
        }
        self.manager.state.state["paper_trading"]["available_balance"] = 10000.0 # Arbitrary
        
        sell_price = 55000.0
        quantity = 0.1
        
        # Execute Sell
        result = self.manager.place_order("BTC/USDT", "sell", quantity, sell_price, "daily")
        
        self.assertTrue(result["success"])
        
        # Check Slippage (Sell price should be LOWER)
        execution_price = result["price"]
        self.assertLess(execution_price, sell_price)
        self.assertGreaterEqual(execution_price, sell_price * (1 - Config.PAPER_SLIPPAGE_RATE_MAX))
        self.assertLessEqual(execution_price, sell_price * (1 - Config.PAPER_SLIPPAGE_RATE_MIN))
        
        # Check Fees
        transaction_value = quantity * execution_price
        fee = result["fee"]
        self.assertAlmostEqual(fee, transaction_value * Config.PAPER_FEE_RATE, places=2)
        
        # Check Proceeds logic inside place_order (indirectly via balance if needed, but return values are enough here)

if __name__ == '__main__':
    unittest.main()
