
import sqlite3
import json
import time
from config import Config
from surreal_client import SurrealClient
import logging

logger = logging.getLogger(__name__)

class TradeDatabase:
    def __init__(self):
        self.db_path = Config.DB_FILE
        self._init_db()
        self.surreal = SurrealClient()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Create the trades table if it doesn't exist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Create trades table
            # Storing complex data (like indicators) as JSON strings
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    size REAL,
                    pnl REAL,
                    pnl_percent REAL,
                    entry_time REAL,
                    exit_time REAL,
                    entry_reason TEXT,
                    exit_reason TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    regime TEXT,
                    market_context JSON, -- Store snapshot of indicators at entry
                    analysis_context JSON, -- Store Opus analysis data
                    status TEXT DEFAULT 'OPEN' -- OPEN, CLOSED
                )
            ''')
            
            # Create screening_log table for DeepSeek pre-screening
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS screening_log (
                    id TEXT PRIMARY KEY,
                    timestamp REAL,
                    symbol TEXT,
                    timeframe TEXT,
                    model TEXT,
                    signal TEXT,
                    confidence TEXT,
                    reasoning TEXT,
                    proceed INTEGER,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    raw_response TEXT,
                    escalated_to_opus INTEGER DEFAULT 0
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"Database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    def log_trade_entry(self, trade_data):
        """
        Logs a new trade entry.
        trade_data: dict containing trade details
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = '''
                INSERT INTO trades (
                    id, symbol, timeframe, side, entry_price, size, 
                    entry_time, entry_reason, stop_loss, take_profit, 
                    regime, market_context, analysis_context, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
            params = (
                trade_data.get('order_id') or str(time.time()), # Use order_id or timestamp as ID
                trade_data['symbol'],
                trade_data['timeframe'],
                trade_data['side'],
                trade_data['entry_price'],
                trade_data['size'],
                trade_data['timestamp'],
                trade_data.get('reason', 'Signal'),
                trade_data['stop_loss'],
                trade_data['take_profit'],
                trade_data.get('regime', 'UNKNOWN'),
                json.dumps(trade_data.get('market_context', {})),
                json.dumps(trade_data.get('analysis_context', {})),
                'OPEN'
            )
            
            cursor.execute(query, params)
            conn.commit()
            conn.close()
            logger.info(f"Logged trade entry for {trade_data['symbol']}")
            
            # SurrealDB shadow write
            self.surreal.upsert_trade(
                trade_id=str(trade_data.get("order_id") or str(time.time())),
                record={
                    "symbol": trade_data["symbol"],
                    "timeframe": trade_data["timeframe"],
                    "side": trade_data["side"],
                    "entry_price": trade_data["entry_price"],
                    "exit_price": None,
                    "size": trade_data["size"],
                    "pnl": None,
                    "pnl_percent": None,
                    "entry_time": trade_data["timestamp"],
                    "exit_time": None,
                    "entry_reason": trade_data.get("reason", "Signal"),
                    "exit_reason": None,
                    "stop_loss": trade_data["stop_loss"],
                    "take_profit": trade_data["take_profit"],
                    "regime": trade_data.get("regime", "UNKNOWN"),
                    "market_context": trade_data.get("market_context", {}),
                    "analysis_context": trade_data.get("analysis_context", {}),
                    "status": "OPEN"
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to log trade entry: {e}")

    def update_trade_exit(self, trade_id, exit_data):
        """
        Updates a trade with exit details.
        exit_data: {exit_price, exit_reason, pnl, pnl_percent, exit_time}
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = '''
                UPDATE trades 
                SET 
                    exit_price = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    pnl = ?,
                    pnl_percent = ?,
                    status = 'CLOSED'
                WHERE id = ?
            '''
            
            params = (
                exit_data['exit_price'],
                exit_data.get('exit_time', time.time()),
                exit_data['exit_reason'],
                exit_data['pnl'],
                exit_data['pnl_percent'],
                trade_id
            )
            
            cursor.execute(query, params)
            
            if cursor.rowcount == 0:
                logger.warning(f"No open trade found with ID {trade_id} to update exit.")
            else:
                conn.commit()
                logger.info(f"Updated trade exit for ID {trade_id}")
                
                # SurrealDB shadow write (partial merge for exit fields only)
                self.surreal.merge_trade(
                    trade_id=str(trade_id),
                    partial={
                        "exit_price": exit_data["exit_price"],
                        "exit_time": exit_data.get("exit_time", time.time()),
                        "exit_reason": exit_data["exit_reason"],
                        "pnl": exit_data["pnl"],
                        "pnl_percent": exit_data["pnl_percent"],
                        "status": "CLOSED"
                    }
                )
                
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update trade exit: {e}")

    def get_recent_trades(self, limit=50):
        """
        Fetches recent closed trades for analysis.
        """
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row # Access columns by name
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM trades 
                WHERE status = 'CLOSED' 
                ORDER BY exit_time DESC 
                LIMIT ?
            ''', (limit,))
            
            rows = cursor.fetchall()
            trades = [dict(row) for row in rows]
            conn.close()
            return trades
        except Exception as e:
            logger.error(f"Failed to fetch recent trades: {e}")
            return []
    
    def log_screening(self, data):
        """
        Logs a DeepSeek screening result.
        data: dict containing screening details
        Returns: screening_id for later escalation update
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = '''
                INSERT INTO screening_log (
                    id, timestamp, symbol, timeframe, model, signal, 
                    confidence, reasoning, proceed, prompt_tokens, 
                    completion_tokens, raw_response, escalated_to_opus
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            '''
            
            params = (
                data.get('id') or str(time.time()),
                data.get('timestamp', time.time()),
                data.get('symbol'),
                data.get('timeframe'),
                data.get('model'),
                data.get('signal'),
                data.get('confidence'),
                data.get('reasoning'),
                1 if data.get('proceed') else 0,
                data.get('prompt_tokens', 0),
                data.get('completion_tokens', 0),
                data.get('raw_response', '')
            )
            
            cursor.execute(query, params)
            conn.commit()
            screening_id = params[0]
            conn.close()
            logger.info(f"Logged screening for {data.get('symbol')} ({data.get('signal')})")
            
            # SurrealDB shadow write
            self.surreal.upsert_screening(
                screening_id=str(data.get("id") or str(time.time())),
                record={
                    "timestamp": data.get("timestamp", time.time()),
                    "symbol": data.get("symbol"),
                    "timeframe": data.get("timeframe"),
                    "model": data.get("model"),
                    "signal": data.get("signal"),
                    "confidence": data.get("confidence"),
                    "reasoning": data.get("reasoning"),
                    "proceed": bool(data.get("proceed")),
                    "prompt_tokens": data.get("prompt_tokens", 0),
                    "completion_tokens": data.get("completion_tokens", 0),
                    "raw_response": data.get("raw_response", ""),
                    "escalated_to_opus": False
                }
            )
            return screening_id
            
        except Exception as e:
            logger.error(f"Failed to log screening: {e}")
            return None
    
    def update_screening_escalated(self, screening_id):
        """
        Updates a screening log entry to mark that it escalated to Opus.
        screening_id: The ID of the screening log entry to update
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = '''
                UPDATE screening_log 
                SET escalated_to_opus = 1
                WHERE id = ?
            '''
            
            cursor.execute(query, (screening_id,))
            
            if cursor.rowcount == 0:
                logger.warning(f"No screening log found with ID {screening_id} to update escalation.")
            else:
                conn.commit()
                logger.info(f"Marked screening {screening_id} as escalated to Opus")
                
                # SurrealDB shadow write
                self.surreal.update_screening_escalated(str(screening_id))
                
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update screening escalation: {e}")
