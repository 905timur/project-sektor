import json
import os
import time
from datetime import datetime
from config import Config

class StateManager:
    def __init__(self):
        self.file_path = Config.STATE_FILE
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding state file. Starting fresh.")
                return self._get_default_state()
        else:
            return self._get_default_state()

    def _get_default_state(self):
        return {
            "capital": {
                "initial": 0.0,
                "current": 0.0,
                "currency": "USDT"
            },
            "positions": {
                "daily": None,  # { symbol: "BTC/USDT", entry_price: 50000, size: 0.01, timestamp: ... }
                "weekly": None
            },
            "performance": {
                "total_pnl": 0.0,
                "win_count": 0,
                "loss_count": 0,
                "weekly_loss": 0.0,
                "week_start_date": datetime.now().strftime("%Y-%m-%d")
            },
            "costs": {
                "total_api_cost": 0.0,
                "daily_api_cost": 0.0,
                "last_reset_date": datetime.now().strftime("%Y-%m-%d")
            },
            "watching": {}, # Persistent watchlist for opportunities
            "paper_trading": {
                "initial_balance": Config.PAPER_TRADING_INITIAL_BALANCE,
                "balance": Config.PAPER_TRADING_INITIAL_BALANCE,
                "available_balance": Config.PAPER_TRADING_INITIAL_BALANCE,
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
        }

    def save_state(self):
        with open(self.file_path, 'w') as f:
            json.dump(self.state, f, indent=4)

    def update_capital(self, amount):
        self.state["capital"]["current"] = amount
        self.save_state()

    def get_position(self, timeframe):
        return self.state["positions"].get(timeframe)

    def set_position(self, timeframe, position_data):
        # Ensure tracking fields exist
        if "highest_price" not in position_data:
            position_data["highest_price"] = position_data["entry_price"]
        
        self.state["positions"][timeframe] = position_data
        self.save_state()

    def update_position(self, timeframe, updates):
        """
        Updates specific fields of an existing position.
        """
        if self.state["positions"].get(timeframe):
            self.state["positions"][timeframe].update(updates)
            self.save_state()

    def clear_position(self, timeframe):
        self.state["positions"][timeframe] = None
        self.save_state()

    def add_cost(self, cost_usd):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state["costs"]["last_reset_date"] != today:
             self.state["costs"]["daily_api_cost"] = 0.0
             self.state["costs"]["last_reset_date"] = today
        
        self.state["costs"]["daily_api_cost"] += cost_usd
        self.state["costs"]["total_api_cost"] += cost_usd
        self.save_state()

    def check_cost_limit(self):
        return self.state["costs"]["daily_api_cost"] < Config.COST_LIMIT_DAILY_USD

    def record_trade(self, pnl_usd):
        self.state["performance"]["total_pnl"] += pnl_usd
        if pnl_usd > 0:
            self.state["performance"]["win_count"] += 1
        else:
            self.state["performance"]["loss_count"] += 1
            
        # Check weekly reset
        # Simple verify if week changed (this is a simplified logic, can be improved)
        # For now, just accumulating loss for safety
        if pnl_usd < 0:
            self.state["performance"]["weekly_loss"] += abs(pnl_usd)
            
        self.save_state()

    def reset_weekly_stats(self):
        self.state["performance"]["weekly_loss"] = 0.0
        self.state["performance"]["week_start_date"] = datetime.now().strftime("%Y-%m-%d")
        self.save_state()

    def get_watching_opportunities(self):
        return self.state.get("watching", {})

    def save_watching_opportunities(self, watching_dict):
        self.state["watching"] = watching_dict
        self.save_state()
