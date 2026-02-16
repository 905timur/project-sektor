
import unittest
import os
import shutil
import sqlite3
import time
import sys
import json

# Add parent directory and files directory to path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'files'))

from database import TradeDatabase
from config import Config

class TestTradeDatabase(unittest.TestCase):
    def setUp(self):
        # Use a temporary DB file
        self.test_db_path = "test_trades.db"
        Config.DB_FILE = self.test_db_path
        self.db = TradeDatabase()

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_log_and_retrieve_trade(self):
        # 1. Log Entry
        trade_id = "test_trade_123"
        entry_data = {
            "order_id": trade_id,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "side": "buy",
            "entry_price": 50000.0,
            "size": 0.1,
            "timestamp": time.time(),
            "stop_loss": 49000.0,
            "take_profit": 52000.0,
            "regime": "TRENDING_UP",
            "market_context": {"rsi": 45},
            "analysis_context": {"signal": "BUY", "confidence": "HIGH"},
            "reason": "Test Signal"
        }
        self.db.log_trade_entry(entry_data)

        # 2. Verify Entry
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "BTC/USDT") # Symbol
        self.assertEqual(row[18], "OPEN") # Status

        # 3. Log Exit
        exit_data = {
            "exit_price": 51000.0,
            "exit_reason": "Take Profit",
            "pnl": 100.0,
            "pnl_percent": 2.0,
            "exit_time": time.time()
        }
        self.db.update_trade_exit(trade_id, exit_data)

        # 4. Verify Exit
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
        row = cursor.fetchone()
        conn.close()

        self.assertEqual(row[5], 51000.0) # Exit Price
        self.assertEqual(row[18], "CLOSED") # Status

        # 5. Verify Get Recent Trades
        recent_trades = self.db.get_recent_trades(limit=10)
        self.assertEqual(len(recent_trades), 1)
        self.assertEqual(recent_trades[0]['id'], trade_id)
        self.assertEqual(recent_trades[0]['pnl'], 100.0)
        
        print("Test passed: Trade logged, updated, and retrieved successfully.")

if __name__ == '__main__':
    unittest.main()
