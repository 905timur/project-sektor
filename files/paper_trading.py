"""
Paper Trading Manager for simulated trading operations.
Tracks balance, positions, PnL, and provides detailed reporting.
"""
import logging
import time
from datetime import datetime
from typing import Dict, Optional, List
from config import Config

logger = logging.getLogger(__name__)


class PaperTradingManager:
    """
    Manages paper trading operations with simulated order execution,
    balance tracking, and comprehensive reporting.
    """
    
    def __init__(self, state_manager):
        """
        Initialize paper trading manager.
        
        Args:
            state_manager: StateManager instance for persisting state
        """
        self.state = state_manager
        self._initialize_paper_account()
    
    def _initialize_paper_account(self):
        """Initialize or verify paper trading account state."""
        paper_state = self.state.state.get("paper_trading", {})
        
        if not paper_state or "balance" not in paper_state:
            # Initialize fresh paper trading account
            self.state.state["paper_trading"] = {
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
            self.state.save_state()
            logger.info(f"📊 Paper Trading Account initialized with ${Config.PAPER_TRADING_INITIAL_BALANCE:.2f}")
    
    def get_balance(self) -> float:
        """Get current paper trading balance."""
        return self.state.state["paper_trading"]["balance"]
    
    def get_available_balance(self) -> float:
        """Get available balance (not in positions)."""
        return self.state.state["paper_trading"]["available_balance"]
    
    def place_order(self, symbol: str, side: str, quantity: float, price: float, 
                    timeframe: str, stop_loss: float = None, take_profit: float = None) -> Dict:
        """
        Simulate placing an order.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            quantity: Amount to trade
            price: Current market price
            timeframe: "daily" or "weekly"
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            Dict with order details
        """
        paper = self.state.state["paper_trading"]
        
        if side.lower() == "buy":
            # Calculate cost
            cost = quantity * price
            
            if cost > paper["available_balance"]:
                logger.warning(f"⚠️ Insufficient paper balance: ${cost:.2f} > ${paper['available_balance']:.2f}")
                return {"success": False, "error": "Insufficient balance"}
            
            # Deduct from available balance
            paper["available_balance"] -= cost
            
            # Create position
            position = {
                "symbol": symbol,
                "side": side.lower(),
                "quantity": quantity,
                "entry_price": price,
                "cost": cost,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "highest_price": price,
                "opened_at": time.time(),
                "timeframe": timeframe
            }
            
            paper["positions"][timeframe] = position
            paper["trades_executed"] += 1
            
            logger.info(f"📈 PAPER BUY: {quantity:.6f} {symbol} @ ${price:.2f} = ${cost:.2f}")
            
        elif side.lower() == "sell":
            # Close position
            position = paper["positions"].get(timeframe)
            if not position:
                logger.warning(f"⚠️ No position to close for {timeframe}")
                return {"success": False, "error": "No position to close"}
            
            # Calculate proceeds and PnL
            proceeds = quantity * price
            entry_cost = position["cost"]
            pnl = proceeds - entry_cost
            pnl_percent = (pnl / entry_cost) * 100 if entry_cost > 0 else 0
            
            # Update balance
            paper["balance"] += pnl
            paper["available_balance"] += proceeds
            
            # Update PnL tracking
            paper["realized_pnl"] += pnl
            if pnl > 0:
                paper["total_profit"] += pnl
                paper["winning_trades"] += 1
            else:
                paper["total_loss"] += abs(pnl)
                paper["losing_trades"] += 1
            
            # Record trade in history
            trade_record = {
                "symbol": symbol,
                "side": "sell",
                "quantity": quantity,
                "entry_price": position["entry_price"],
                "exit_price": price,
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "opened_at": position["opened_at"],
                "closed_at": time.time(),
                "duration_hours": (time.time() - position["opened_at"]) / 3600,
                "timeframe": timeframe
            }
            paper["trade_history"].append(trade_record)
            
            # Clear position
            paper["positions"][timeframe] = None
            
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            logger.info(f"📉 PAPER SELL: {quantity:.6f} {symbol} @ ${price:.2f} = ${proceeds:.2f} | PnL: {pnl_emoji} ${pnl:.2f} ({pnl_percent:.2f}%)")
        
        self.state.save_state()
        return {"success": True, "side": side, "quantity": quantity, "price": price}
    
    def update_position_prices(self, timeframe: str, current_price: float):
        """
        Update highest price for trailing stop calculations.
        """
        paper = self.state.state["paper_trading"]
        position = paper["positions"].get(timeframe)
        
        if position and current_price > position.get("highest_price", 0):
            position["highest_price"] = current_price
            self.state.save_state()
    
    def get_position(self, timeframe: str) -> Optional[Dict]:
        """Get current position for a timeframe."""
        return self.state.state["paper_trading"]["positions"].get(timeframe)
    
    def get_unrealized_pnl(self, timeframe: str, current_price: float) -> Optional[Dict]:
        """
        Calculate unrealized PnL for an open position.
        
        Returns:
            Dict with unrealized_pnl, unrealized_pnl_percent, or None if no position
        """
        position = self.get_position(timeframe)
        if not position:
            return None
        
        current_value = position["quantity"] * current_price
        unrealized_pnl = current_value - position["cost"]
        unrealized_pnl_percent = (unrealized_pnl / position["cost"]) * 100 if position["cost"] > 0 else 0
        
        return {
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_percent": unrealized_pnl_percent,
            "current_value": current_value
        }
    
    def get_summary(self) -> Dict:
        """
        Get comprehensive trading summary.
        
        Returns:
            Dict with all trading statistics
        """
        paper = self.state.state["paper_trading"]
        
        total_trades = paper["trades_executed"]
        winning_trades = paper["winning_trades"]
        losing_trades = paper["losing_trades"]
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        return {
            "initial_balance": paper["initial_balance"],
            "current_balance": paper["balance"],
            "available_balance": paper["available_balance"],
            "total_profit": paper["total_profit"],
            "total_loss": paper["total_loss"],
            "net_pnl": paper["realized_pnl"],
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "open_positions": sum(1 for p in paper["positions"].values() if p is not None),
            "trade_history_count": len(paper["trade_history"]),
            "started_at": paper["started_at"]
        }
    
    def generate_report(self) -> str:
        """
        Generate a detailed text report for logging.
        
        Returns:
            Formatted report string
        """
        summary = self.get_summary()
        paper = self.state.state["paper_trading"]
        
        # Calculate return percentage
        return_pct = ((summary["current_balance"] - summary["initial_balance"]) / 
                      summary["initial_balance"] * 100) if summary["initial_balance"] > 0 else 0
        
        report_lines = [
            "=" * 50,
            "📊 PAPER TRADING REPORT",
            "=" * 50,
            f"💰 Balance: ${summary['current_balance']:.2f} (Started: ${summary['initial_balance']:.2f})",
            f"📈 Return: {return_pct:+.2f}% (${summary['net_pnl']:+.2f})",
            f"💵 Available: ${summary['available_balance']:.2f}",
            "-" * 50,
            f"✅ Total Profit: ${summary['total_profit']:.2f}",
            f"❌ Total Loss: ${summary['total_loss']:.2f}",
            f"📊 Net PnL: ${summary['net_pnl']:+.2f}",
            "-" * 50,
            f"🔢 Total Trades: {summary['total_trades']}",
            f"🟢 Winning: {summary['winning_trades']} | 🔴 Losing: {summary['losing_trades']}",
            f"🎯 Win Rate: {summary['win_rate']:.1f}%",
            f"📍 Open Positions: {summary['open_positions']}",
            "-" * 50,
        ]
        
        # Add open positions details
        for tf, pos in paper["positions"].items():
            if pos:
                report_lines.extend([
                    f"📌 {tf.upper()} Position:",
                    f"   {pos['symbol']}: {pos['quantity']:.6f} @ ${pos['entry_price']:.2f}",
                    f"   Cost: ${pos['cost']:.2f} | SL: ${pos.get('stop_loss', 'N/A')} | TP: ${pos.get('take_profit', 'N/A')}"
                ])
        
        # Add recent trades (last 5)
        if paper["trade_history"]:
            report_lines.append("-" * 50)
            report_lines.append("📜 Recent Trades:")
            for trade in paper["trade_history"][-5:]:
                pnl_emoji = "🟢" if trade["pnl"] >= 0 else "🔴"
                report_lines.append(
                    f"   {trade['symbol']}: {pnl_emoji} ${trade['pnl']:+.2f} ({trade['pnl_percent']:+.2f}%)"
                )
        
        report_lines.append("=" * 50)
        return "\n".join(report_lines)
    
    def log_report(self):
        """Log the current trading report."""
        report = self.generate_report()
        for line in report.split("\n"):
            logger.info(line)
    
    def get_trade_history(self, limit: int = 20) -> List[Dict]:
        """Get recent trade history."""
        return self.state.state["paper_trading"]["trade_history"][-limit:]
