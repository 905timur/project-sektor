"""
Tests for Phase 2 SurrealDB Read Layer.

These tests verify:
1. Belief retrieval uses SurrealDB when available
2. Fallback to JSON on SurrealDB failure or empty results
3. Trade context retrieval uses SurrealDB when available
4. Fallback to SQLite on SurrealDB failure
5. Backward compatibility for run_analysis() with no args
6. Targeted retrieval when conditions are provided
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import os
import sys

# Add parent directory and files directory to path (matching existing test patterns)
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, 'files'))


class TestBeliefRetrieval(unittest.TestCase):
    """Tests for get_relevant_beliefs with SurrealDB-first approach."""
    
    def test_get_relevant_beliefs_uses_surrealdb_when_available(self):
        """Test that SurrealDB is used when available and returns results."""
        # Need to mock before importing
        with patch('config.Config') as MockConfig:
            MockConfig.BELIEFS_INJECTED_PER_ANALYSIS = 5
            
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_instance = Mock()
                mock_beliefs = [
                    {
                        "id": "belief_1",
                        "symbol": "BTC/USDT",
                        "timeframe": "daily",
                        "outcome": "WIN",
                        "pnl_percent": 2.5,
                        "belief": "Test belief 1",
                        "tags": ["TRENDING_UP"],
                        "confidence_in_belief": "HIGH",
                        "timestamp": 1234567890,
                        "_regime_match": True
                    },
                    {
                        "id": "belief_2", 
                        "symbol": "BTC/USDT",
                        "timeframe": "daily",
                        "outcome": "WIN",
                        "pnl_percent": 1.8,
                        "belief": "Test belief 2",
                        "tags": ["TRENDING_UP"],
                        "confidence_in_belief": "MEDIUM",
                        "timestamp": 1234567880,
                        "_regime_match": True
                    },
                    {
                        "id": "belief_3",
                        "symbol": "BTC/USDT", 
                        "timeframe": "daily",
                        "outcome": "LOSS",
                        "pnl_percent": -1.2,
                        "belief": "Test belief 3",
                        "tags": ["RANGING"],
                        "confidence_in_belief": "LOW",
                        "timestamp": 1234567870,
                        "_regime_match": False
                    }
                ]
                mock_instance.query_relevant_beliefs.return_value = mock_beliefs
                MockSurreal.return_value = mock_instance
                
                from belief_manager import BeliefManager
                
                # Patch load_beliefs to ensure it's NOT called
                with patch.object(BeliefManager, 'load_beliefs') as mock_load:
                    manager = BeliefManager()
                    manager.surreal = mock_instance
                    
                    result = manager.get_relevant_beliefs(
                        symbol="BTC/USDT",
                        timeframe="daily", 
                        regime="TRENDING_UP"
                    )
                    
                    # Assert SurrealDB was called and returned results
                    mock_instance.query_relevant_beliefs.assert_called_once_with(
                        symbol="BTC/USDT",
                        timeframe="daily",
                        regime="TRENDING_UP",
                        setup_type=None,
                        n=5
                    )
                    
                    # load_beliefs should NOT be called (SurrealDB path taken)
                    mock_load.assert_not_called()
                    
                    # Assert results match mock
                    self.assertEqual(len(result), 3)
                    self.assertEqual(result[0]["id"], "belief_1")

    def test_get_relevant_beliefs_falls_back_to_json_on_surreal_failure(self):
        """Test fallback to JSON scoring when SurrealDB raises an exception."""
        json_beliefs = [
            {"id": "b1", "symbol": "BTC/USDT", "timeframe": "daily", "tags": ["TRENDING_UP"], "timestamp": 1234567890},
            {"id": "b2", "symbol": "BTC/USDT", "timeframe": "daily", "tags": ["RANGING"], "timestamp": 1234567880},
            {"id": "b3", "symbol": "ETH/USDT", "timeframe": "weekly", "tags": [], "timestamp": 1234567870},
            {"id": "b4", "symbol": "BTC/USDT", "timeframe": "daily", "tags": ["TRENDING_UP"], "timestamp": 1234567860},
            {"id": "b5", "symbol": "SOL/USDT", "timeframe": "daily", "tags": [], "timestamp": 1234567850},
        ]
        
        with patch('config.Config') as MockConfig:
            MockConfig.BELIEFS_INJECTED_PER_ANALYSIS = 5
            
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_instance = Mock()
                # SurrealDB raises exception
                mock_instance.query_relevant_beliefs.side_effect = Exception("timeout")
                MockSurreal.return_value = mock_instance
                
                from belief_manager import BeliefManager
                
                with patch('belief_manager.open', create=True) as mock_open:
                    # Mock file reading for load_beliefs
                    mock_file = MagicMock()
                    mock_file.__enter__.return_value = mock_file
                    mock_file.__exit__.return_value = False
                    mock_file.read.return_value = json.dumps({"beliefs": json_beliefs})
                    mock_open.return_value = mock_file
                    
                    manager = BeliefManager()
                    manager.surreal = mock_instance
                    
                    result = manager.get_relevant_beliefs(
                        symbol="BTC/USDT",
                        timeframe="daily",
                        regime="TRENDING_UP"
                    )
                    
                    # Should have returned results from JSON fallback
                    self.assertEqual(len(result), 5)  # All 5 beliefs
                    # First should be highest scoring (same symbol + timeframe + regime tag)
                    self.assertEqual(result[0]["id"], "b1")

    def test_get_relevant_beliefs_falls_back_to_json_on_empty_surreal(self):
        """Test fallback to JSON when SurrealDB returns empty list."""
        json_beliefs = [
            {"id": "b1", "symbol": "BTC/USDT", "timeframe": "daily", "tags": ["TRENDING_UP"], "timestamp": 1234567890},
            {"id": "b2", "symbol": "BTC/USDT", "timeframe": "daily", "tags": ["RANGING"], "timestamp": 1234567880},
            {"id": "b3", "symbol": "ETH/USDT", "timeframe": "weekly", "tags": [], "timestamp": 1234567870},
        ]
        
        with patch('config.Config') as MockConfig:
            MockConfig.BELIEFS_INJECTED_PER_ANALYSIS = 5
            
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_instance = Mock()
                # SurrealDB returns empty list
                mock_instance.query_relevant_beliefs.return_value = []
                MockSurreal.return_value = mock_instance
                
                from belief_manager import BeliefManager
                
                with patch('belief_manager.open', create=True) as mock_open:
                    mock_file = MagicMock()
                    mock_file.__enter__.return_value = mock_file
                    mock_file.__exit__.return_value = False
                    mock_file.read.return_value = json.dumps({"beliefs": json_beliefs})
                    mock_open.return_value = mock_file
                    
                    manager = BeliefManager()
                    manager.surreal = mock_instance
                    
                    result = manager.get_relevant_beliefs(
                        symbol="BTC/USDT",
                        timeframe="daily",
                        regime="TRENDING_UP"
                    )
                    
                    # Should have returned 3 results from JSON fallback
                    self.assertEqual(len(result), 3)


class TestTradeConditionMatching(unittest.TestCase):
    """Tests for condition-matched trade retrieval."""
    
    def test_get_condition_matched_trades_uses_surrealdb(self):
        """Test SurrealDB is used when available for trade retrieval."""
        mock_trades = [
            {"id": "t1", "symbol": "BTC/USDT", "regime": "TRENDING_UP", "timeframe": "daily", "status": "CLOSED"},
            {"id": "t2", "symbol": "BTC/USDT", "regime": "TRENDING_UP", "timeframe": "daily", "status": "CLOSED"},
            {"id": "t3", "symbol": "BTC/USDT", "regime": "TRENDING_UP", "timeframe": "daily", "status": "CLOSED"},
            {"id": "t4", "symbol": "BTC/USDT", "regime": "TRENDING_UP", "timeframe": "daily", "status": "CLOSED"},
            {"id": "t5", "symbol": "BTC/USDT", "regime": "TRENDING_UP", "timeframe": "daily", "status": "CLOSED"},
        ]
        
        with patch('database.TradeDatabase'):
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_instance = Mock()
                mock_instance.query_trades_by_conditions.return_value = mock_trades
                MockSurreal.return_value = mock_instance
                
                with patch('state_manager.StateManager'):
                    with patch('anthropic.Anthropic'):
                        with patch('config.Config'):
                            # Need to reimport after patches
                            from analyze_trades import TradeAnalyzer
                            
                            analyzer = TradeAnalyzer()
                            
                            trades, source = analyzer.get_condition_matched_trades(
                                symbol="BTC/USDT",
                                regime="TRENDING_UP",
                                timeframe="daily"
                            )
                            
                            self.assertEqual(source, "surrealdb")
                            self.assertEqual(len(trades), 5)
                            mock_instance.query_trades_by_conditions.assert_called_once()

    def test_get_condition_matched_trades_falls_back_to_sqlite(self):
        """Test fallback to SQLite when SurrealDB fails."""
        mock_trades = [
            {"id": "t1", "symbol": "BTC/USDT", "regime": "RANGING", "status": "CLOSED"},
            {"id": "t2", "symbol": "BTC/USDT", "regime": "RANGING", "status": "CLOSED"},
            {"id": "t3", "symbol": "ETH/USDT", "regime": "RANGING", "status": "CLOSED"},
        ]
        
        with patch('database.TradeDatabase') as MockDB:
            mock_db_instance = Mock()
            mock_db_instance.get_trades_by_conditions.return_value = mock_trades
            MockDB.return_value = mock_db_instance
            
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_surreal_instance = Mock()
                # SurrealDB raises exception
                mock_surreal_instance.query_trades_by_conditions.side_effect = Exception("timeout")
                MockSurreal.return_value = mock_surreal_instance
                
                with patch('state_manager.StateManager'):
                    with patch('anthropic.Anthropic'):
                        with patch('config.Config'):
                            from analyze_trades import TradeAnalyzer
                            
                            analyzer = TradeAnalyzer()
                            analyzer.db = mock_db_instance
                            
                            trades, source = analyzer.get_condition_matched_trades(
                                symbol="BTC/USDT",
                                regime="RANGING"
                            )
                            
                            self.assertEqual(source, "sqlite")
                            self.assertEqual(len(trades), 3)


class TestRunAnalysis(unittest.TestCase):
    """Tests for run_analysis backward compatibility and new features."""
    
    def test_run_analysis_no_args_unchanged_behaviour(self):
        """Test run_analysis with no args uses original behavior."""
        mock_trades = [
            {"id": "t1", "symbol": "BTC/USDT", "status": "CLOSED"},
            {"id": "t2", "symbol": "ETH/USDT", "status": "CLOSED"},
        ]
        
        mock_response = Mock()
        mock_response.content = [Mock(text="Test analysis report")]
        
        with patch('database.TradeDatabase') as MockDB:
            mock_db_instance = Mock()
            mock_db_instance.get_recent_trades.return_value = mock_trades
            MockDB.return_value = mock_db_instance
            
            with patch('state_manager.StateManager'):
                with patch('anthropic.Anthropic') as MockAnthropic:
                    mock_client = Mock()
                    mock_client.messages.create.return_value = mock_response
                    MockAnthropic.return_value = mock_client
                    
                    with patch('config.Config') as MockConfig:
                        MockConfig.ANALYSIS_MODEL = "claude-opus-4-5-20251101"
                        MockConfig.DATA_DIR = "/tmp"
                        
                        from analyze_trades import TradeAnalyzer
                        
                        analyzer = TradeAnalyzer()
                        analyzer.db = mock_db_instance
                        analyzer.client = mock_client
                        
                        # Call with no args
                        analyzer.run_analysis()
                        
                        # Verify get_recent_trades was called (not condition-matched)
                        mock_db_instance.get_recent_trades.assert_called_once_with(limit=50)
                        
                        # Verify no condition prefix was added to prompt
                        call_args = mock_client.messages.create.call_args
                        prompt = call_args.kwargs.get('messages', [{}])[0].get('content', '')
                        self.assertFalse(prompt.startswith('[TARGETED ANALYSIS'))

    def test_run_analysis_with_conditions_uses_targeted_retrieval(self):
        """Test run_analysis with conditions uses condition-matched retrieval."""
        # Empty trades to test early exit path
        mock_trades = []
        
        with patch('database.TradeDatabase') as MockDB:
            mock_db_instance = Mock()
            MockDB.return_value = mock_db_instance
            
            with patch('surreal_client.SurrealClient') as MockSurreal:
                mock_surreal_instance = Mock()
                mock_surreal_instance.query_trades_by_conditions.return_value = mock_trades
                MockSurreal.return_value = mock_surreal_instance
                
                with patch('state_manager.StateManager'):
                    with patch('anthropic.Anthropic') as MockAnthropic:
                        mock_client = Mock()
                        MockAnthropic.return_value = mock_client
                        
                        with patch('config.Config') as MockConfig:
                            MockConfig.ANALYSIS_MODEL = "claude-opus-4-5-20251101"
                            MockConfig.DATA_DIR = "/tmp"
                            
                            from analyze_trades import TradeAnalyzer
                            
                            analyzer = TradeAnalyzer()
                            analyzer.db = mock_db_instance
                            analyzer.client = mock_client
                            
                            # Call with conditions - should use targeted retrieval
                            analyzer.run_analysis(
                                symbol="BTC/USDT",
                                regime="TRENDING_UP"
                            )
                            
                            # Verify condition-matched retrieval was attempted
                            # (not get_recent_trades)
                            mock_db_instance.get_recent_trades.assert_not_called()


if __name__ == "__main__":
    unittest.main()
