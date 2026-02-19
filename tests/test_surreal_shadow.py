"""
Unit tests for SurrealDB shadow layer dual-write functionality.

These tests verify:
1. SurrealClient gracefully handles missing package or empty URL
2. Dual-write doesn't block main thread
3. SQLite/JSON writes succeed even if SurrealDB fails
4. Belief manager correctly passes trade_id for graph edges
"""

import os
import sys
import json
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSurrealClientDisabled(unittest.TestCase):
    """Tests for SurrealClient when disabled (package missing or URL empty)."""
    
    def test_surreal_client_disabled_when_package_missing(self):
        """SurrealClient should set enabled=False when surrealdb package is not installed."""
        # Mock the surrealdb import to raise ImportError
        with patch.dict(sys.modules, {'surrealdb': None, 'surrealdb.module': MagicMock()}):
            # Need to reimport to trigger the ImportError handling
            import importlib
            import files.surreal_client as surreal_module
            importlib.reload(surreal_module)
            
            # Patch the SURREALDB_AVAILABLE check
            with patch.object(surreal_module, 'SURREALDB_AVAILABLE', False):
                client = surreal_module.SurrealClient()
                self.assertFalse(client.enabled, "Client should be disabled when package is missing")
                
                # Verify no exception is raised when calling methods
                client.upsert_trade("test_id", {"symbol": "BTC"})
                client.merge_trade("test_id", {"pnl": 100})
                client.upsert_screening("screening_1", {"symbol": "ETH"})
                client.update_screening_escalated("screening_1")
                client.upsert_belief("belief_1", {"belief": "test"}, "trade_1")
                client.upsert_state({"capital": 1000})
    
    def test_surreal_client_disabled_when_url_empty(self):
        """SurrealClient should set enabled=False when SURREALDB_URL is empty."""
        # Patch Config.SURREALDB_URL to return empty string
        with patch('files.surreal_client.SURREALDB_AVAILABLE', True):
            with patch('files.surreal_client.surrealdb') as mock_surreal:
                with patch('files.config.Config') as mock_config:
                    mock_config.SURREALDB_URL = ""
                    
                    import files.surreal_client as surreal_module
                    import importlib
                    importlib.reload(surreal_module)
                    
                    client = surreal_module.SurrealClient()
                    self.assertFalse(client.enabled, "Client should be disabled when URL is empty")


class TestDatabaseDualWrite(unittest.TestCase):
    """Tests for database dual-write functionality."""
    
    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        
        # Patch Config.DB_FILE to use our temp db
        self.db_patch = patch('files.config.Config.DB_FILE', self.temp_db.name)
        self.db_patch.start()
        
        # Import database module after patching
        import files.database as db_module
        import importlib
        importlib.reload(db_module)
        
        # Also patch SurrealClient to track calls
        self.mock_surreal = MagicMock()
        self.surreal_patch = patch.object(db_module, 'SurrealClient', return_value=self.mock_surreal)
        self.surreal_patch.start()
        
        self.db_module = db_module
        self.db = db_module.TradeDatabase()
    
    def tearDown(self):
        """Clean up temporary files."""
        self.db_patch.stop()
        self.surreal_patch.stop()
        
        try:
            os.unlink(self.temp_db.name)
        except:
            pass
    
    def test_database_dual_write_does_not_block(self):
        """Verify that dual-write doesn't block SQLite operations."""
        trade_data = {
            "order_id": "test_order_123",
            "symbol": "BTC/USDT",
            "timeframe": "daily",
            "side": "buy",
            "entry_price": 50000.0,
            "size": 0.01,
            "timestamp": time.time(),
            "reason": "Signal",
            "stop_loss": 49000.0,
            "take_profit": 52000.0,
            "regime": "BULL",
            "market_context": {"rsi": 50},
            "analysis_context": {"signal": "FVG"}
        }
        
        # Call log_trade_entry - should succeed immediately
        self.db_module.log_trade_entry(self.db, trade_data)
        
        # Verify SQLite write succeeded
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = ?", ("test_order_123",))
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row, "Trade should exist in SQLite")
        self.assertEqual(row[1], "BTC/USDT", "Symbol should match")
        
        # Verify SurrealDB shadow write was called
        self.mock_surreal.upsert_trade.assert_called_once()
        call_args = self.mock_surreal.upsert_trade.call_args
        self.assertEqual(call_args[1]["trade_id"], "test_order_123")
    
    def test_surreal_failure_does_not_affect_sqlite(self):
        """SQLite should succeed even if SurrealDB upsert raises an exception."""
        # Make SurrealDB raise an exception
        self.mock_surreal.upsert_trade.side_effect = Exception("Connection refused")
        
        trade_data = {
            "order_id": "test_order_456",
            "symbol": "ETH/USDT",
            "timeframe": "weekly",
            "side": "sell",
            "entry_price": 3000.0,
            "size": 0.1,
            "timestamp": time.time(),
            "reason": "Signal",
            "stop_loss": 2900.0,
            "take_profit": 3200.0,
            "regime": "BEAR",
            "market_context": {},
            "analysis_context": {}
        }
        
        # This should NOT raise - SurrealDB failure should be silent
        self.db_module.log_trade_entry(self.db, trade_data)
        
        # Verify SQLite still has the trade
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = ?", ("test_order_456",))
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row, "Trade should exist in SQLite despite SurrealDB failure")


class TestBeliefManagerDualWrite(unittest.TestCase):
    """Tests for belief manager dual-write functionality."""
    
    def setUp(self):
        """Create a temporary beliefs file for testing."""
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        self.temp_file.close()
        
        # Patch Config.BELIEFS_FILE
        self.file_patch = patch('files.config.Config.BELIEFS_FILE', self.temp_file.name)
        self.file_patch.start()
        
        # Initialize with empty beliefs
        with open(self.temp_file.name, 'w') as f:
            json.dump({"beliefs": []}, f)
        
        # Import and reload
        import files.belief_manager as bm_module
        import importlib
        importlib.reload(bm_module)
        
        # Mock SurrealClient
        self.mock_surreal = MagicMock()
        self.surreal_patch = patch.object(bm_module, 'SurrealClient', return_value=self.mock_surreal)
        self.surreal_patch.start()
        
        self.bm_module = bm_module
        self.bm = bm_module.BeliefManager()
    
    def tearDown(self):
        """Clean up temporary files."""
        self.file_patch.stop()
        self.surreal_patch.stop()
        
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_belief_manager_dual_write(self):
        """Belief manager should write to JSON and call SurrealDB with trade_id."""
        belief_dict = {
            "id": "belief_001",
            "timestamp": time.time(),
            "symbol": "BTC/USDT",
            "timeframe": "daily",
            "outcome": "WIN",
            "pnl_percent": 5.5,
            "belief": "Price often retraces to FVG before continuation",
            "tags": ["FVG", "retracement"],
            "confidence_in_belief": "HIGH",
            "trade_id": "trade_abc_123"
        }
        
        # Add belief
        self.bm_module.add_belief(self.bm, belief_dict)
        
        # Verify JSON was written
        with open(self.temp_file.name, 'r') as f:
            data = json.load(f)
        
        self.assertEqual(len(data["beliefs"]), 1, "Belief should be in JSON")
        self.assertEqual(data["beliefs"][0]["id"], "belief_001")
        
        # Verify SurrealDB was called with trade_id
        self.mock_surreal.upsert_belief.assert_called_once()
        call_args = self.mock_surreal.upsert_belief.call_args
        self.assertEqual(call_args[1]["trade_id"], "trade_abc_123")


if __name__ == '__main__':
    unittest.main()
