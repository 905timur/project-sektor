
import sqlite3
import json
import time
from config import Config
import logging

logger = logging.getLogger(__name__)

class TradeDatabase:
    def __init__(self):
        self.db_path = Config.DB_FILE
        self._init_db()

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
