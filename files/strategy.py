import time
import logging
from config import Config
from exchange_client import ExchangeClient
from market_data import MarketDataManager
from llm_client import LLMClient
from telegram_bot import TelegramBot
from state_manager import StateManager
from state_manager import StateManager
from opportunity_tracker import OpportunityTracker
from database import TradeDatabase
from paper_trading import PaperTradingManager
from news_client import RSSNewsClient

logger = logging.getLogger(__name__)

class ImbalanceStrategy:
    def __init__(self):
        self.state = StateManager()
        self.db = TradeDatabase() # Initialize DB
        self.tracker = OpportunityTracker(self.state)
        # Initialize clients
        self.exchange = ExchangeClient()
        self.market_data = MarketDataManager(self.exchange)
        
        # Initialize news client for sentiment analysis
        self.news_client = RSSNewsClient()
        logger.info("📰 RSS News Client initialized for sentiment analysis")
        
        self.llm = LLMClient(self.state, news_client=self.news_client)
        self.telegram = TelegramBot()
        
        # Initialize paper trading if enabled
        self.paper_trading = None
        if Config.PAPER_TRADING:
            self.paper_trading = PaperTradingManager(self.state)
            logger.info("📊 Paper Trading Mode ENABLED")
            self.paper_trading.log_report()

    def run_pipeline(self):
        """
        Main strategy pipeline.
        1. Manage Positions
        2. Check Watchlist (Retracements)
        3. Scan for New Imbalances (if not full)
        """
        # 1. Reset costs if new day
        self.state.add_cost(0) 

        # 2. Manage existing positions
        self.manage_positions()

        # 3. Check Watchlist for Retracements (Priority)
        watchlist = self.tracker.get_watch_list()
        for key, opp in list(watchlist.items()): # list() to allow modification
            self.check_watchlist_item(opp)

        # 4. Scan for New Opportunities (if capacity exists)
        open_daily = self.state.get_position("daily")
        open_weekly = self.state.get_position("weekly")

        if not open_daily:
            self.scan_market("daily")
            
        if not open_weekly:
            self.scan_market("weekly")

    def check_watchlist_item(self, opp):
        symbol = opp['symbol']
        tf_name = opp['timeframe']
        
        # Fetch current data (small fetch just to check price)
        # Actually we need full data for context if we trigger analysis
        # But to save API, let's just get the latest candle first?
        # MarketData manager is optimized to fetch 200 candles, which is fine.
        
        df_dict = self.market_data.get_multi_timeframe_data(symbol, tf_name)
        if not df_dict or df_dict['primary'] is None:
            return

        current_candle = df_dict['primary'].iloc[-1]
        
        # Check Retracement Logic
        if self.tracker.check_retracement(symbol, tf_name, current_candle):
            # Ready for Analysis!
            logger.info(f"⚡ Triggering Analysis for {symbol} ({tf_name}) - Retracement Confirmed")
            
            # Phase 2: Add Regime and Context
            primary_df = df_dict['primary']
            regime = self.market_data.detect_market_regime(primary_df)
            sr_levels = self.market_data.identify_support_resistance(primary_df)
            
            # Add to analysis context
            context_extras = {
                'regime': regime,
                'sr_levels': sr_levels
            }
            
            # === Stage 1: DeepSeek Screening ===
            screening = self.llm.screen_with_deepseek(symbol, tf_name, df_dict, context_extras)
            
            if not screening:
                logger.warning(f"⚠️ DeepSeek screening failed for {symbol} - falling back to Opus")
                # Fallback to Opus on complete failure
                screening = {"proceed_to_full_analysis": True, "screening_id": None}
            
            # Log screening result to database
            screening_id = screening.get('screening_id')
            if screening_id:
                self.db.log_screening({
                    'id': screening_id,
                    'symbol': symbol,
                    'timeframe': tf_name,
                    'model': Config.DEEPSEEK_MODEL,
                    'signal': screening.get('signal', 'NEUTRAL'),
                    'confidence': screening.get('confidence', 'LOW'),
                    'reasoning': screening.get('reasoning', ''),
                    'proceed': 1 if screening.get('proceed_to_full_analysis') else 0,
                    'prompt_tokens': screening.get('prompt_tokens', 0),
                    'completion_tokens': screening.get('completion_tokens', 0),
                    'raw_response': screening.get('raw_response', '')
                })
            
            # Log reasoning
            reasoning = screening.get('reasoning', 'No reasoning provided')
            logger.info(f"💬 DeepSeek ({symbol}): {reasoning}")
            
            # Check if we should proceed to Opus
            if not screening.get('proceed_to_full_analysis'):
                signal = screening.get('signal', 'NEUTRAL')
                confidence = screening.get('confidence', 'LOW')
                logger.info(f"⏭️ DeepSeek screened out {symbol} ({signal} / {confidence}) - Opus skipped")
                
                # Leave on watchlist if NEUTRAL or LOW confidence BUY/SELL
                if signal == 'NEUTRAL':
                    pass  # Keep watching
                elif confidence == 'LOW':
                    pass  # Keep watching, might improve
                else:
                    # Shouldn't happen given proceed logic, but handle it
                    self.tracker.remove_opportunity(symbol, tf_name)
                return
            
            # === Stage 2: Opus Analysis ===
            logger.info(f"🔍 DeepSeek approved {symbol} ({screening.get('signal')} / {screening.get('confidence')}) - escalating to Opus")
            
            analysis = self.llm.analyze_opportunity(symbol, tf_name, df_dict, context_extras)
            
            # Update screening log that we escalated to Opus
            if screening_id:
                self.db.update_screening_escalated(screening_id)
            
            if not analysis:
                return
                
            if analysis.get("signal") in ["BUY", "SELL"] and analysis.get("confidence") == "HIGH":
                # Execute!
                self.execute_trade(symbol, tf_name, analysis, context_extras)
                # Remove from watchlist after execution
                self.tracker.remove_opportunity(symbol, tf_name)
            else:
                logger.info(f"Opus rejected {symbol}: {analysis.get('reasoning')}")
                if analysis.get("signal") == "NEUTRAL":
                    pass 
                else:
                    self.tracker.remove_opportunity(symbol, tf_name)


    def scan_market(self, timeframe_name):
        """
        Scans all pairs for NEW imbalances to add to watchlist.
        """
        for symbol in Config.PAIRS:
            # Check if we are already watching this
            if f"{symbol}_{timeframe_name}" in self.tracker.get_watch_list():
                continue
                
            # Fetch Data
            # Just primary needed for detecting the structure first
            tf_code = Config.TIMEFRAMES[timeframe_name]
            df = self.market_data.get_market_data(symbol, tf_code)
            
            if df is None:
                continue

            # Technical Filter (FVG/OB/Extreme)
            # This logic mimics 'market_data.check_imbalance' but returns specific found structure
            
            # Detect FVGs
            fvgs = self.market_data.detect_fair_value_gaps(df, lookback=5)
            if fvgs:
                # Add to watchlist (use the most recent one)
                latest_fvg = fvgs[-1]
                self.tracker.add_opportunity(symbol, timeframe_name, latest_fvg)
                continue

            # Detect Order Blocks
            obs = self.market_data.detect_order_blocks(df, lookback=5)
            if obs:
                latest_ob = obs[-1]
                self.tracker.add_opportunity(symbol, timeframe_name, latest_ob)
                continue
            
            # Fallback Extreme Check (only if high priority enabled? Let's skip fallback for now to ensure quality)
            # Strategy says "Replace simple RSI with actual imbalance" -> So we stick to FVG/OB.

    def execute_trade(self, symbol, timeframe, analysis, context_extras=None):
        """
        Executes the trade and updates state.
        Adjusts position size based on Regime.
        Supports both paper trading and live trading.
        """
        side = analysis.get("signal", "BUY").lower()
        entry_price = analysis.get("entry_target") 
        # If entry target is far from current, we might need a limit order. 
        # For simplicity in this bot, we might use market or aggressive limit.
        # But 'imbalance-bot' normally implies catching a move NOW.
        # Let's assume we place a limit at current price (Post-Only) or Market if urgent.
        
        # Calculate Size - use paper trading balance if enabled
        if Config.PAPER_TRADING and self.paper_trading:
            capital = self.paper_trading.get_available_balance()
        else:
            capital = self.state.state["capital"]["current"] or Config.STARTING_CAPITAL
            # Use simple fixed logic for now if capital is 0 in state
            if capital <= 0:
                 # Fetch from exchange
                 try:
                     capital = self.exchange.get_balance("USDT")
                     self.state.update_capital(capital)
                 except:
                     capital = 100.0 # Fallback/Paper default

        base_size_percent = Config.POSITION_SIZE_PERCENT
        
        # Phase 2: Regime-Based Sizing
        if context_extras:
            regime = context_extras.get('regime', 'RANGING')
            if regime == 'VOLATILE':
                logger.info(f"⚠️ Market is VOLATILE for {symbol}. Reducing position size by 50%.")
                base_size_percent *= 0.5
            elif regime == 'TRENDING_UP' and side == 'sell':
                logger.info(f"⚠️ Counter-trend sell in UP trend. Reducing size.")
                base_size_percent *= 0.5
            elif regime == 'TRENDING_DOWN' and side == 'buy':
                logger.info(f"⚠️ Counter-trend buy in DOWN trend. Reducing size.")
                base_size_percent *= 0.5

        size_usd = capital * base_size_percent
        
        # Ensure minimum size (lower for paper trading)
        min_size = 1.0 if Config.PAPER_TRADING else 10.0
        if size_usd < min_size:
            logger.warning(f"Position size ${size_usd:.2f} too small (min: ${min_size:.2f}).")
            return

        # Fetch current price again for accurate quantity calc
        ticker = self.exchange.get_ticker(symbol)
        current_price = ticker['last']
        quantity = size_usd / current_price
        
        stop_loss = analysis.get("stop_loss")
        take_profit = analysis.get("take_profit")
        
        # Execute - Paper Trading or Live
        if Config.PAPER_TRADING and self.paper_trading:
            # Paper Trading Execution
            order = self.paper_trading.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=current_price,
                timeframe=timeframe,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            if not order.get("success"):
                logger.warning(f"Paper trade execution failed: {order.get('error')}")
                return
                
            order_id = f"PAPER_{time.time()}"
            
            # Update State for compatibility
            position_data = {
                "symbol": symbol,
                "entry_price": current_price,
                "size": quantity,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": time.time(),
                "order_id": order_id,
                "regime": context_extras.get('regime') if context_extras else "UNKNOWN"
            }
            self.state.set_position(timeframe, position_data)
            
            # Notify
            regime_msg = f"Regime: {position_data['regime']}"
            msg = f"[PAPER] Opened {timeframe.upper()} position for {symbol}\nSize: {quantity:.4f}\nEntry: ${current_price:.2f}\nSL: ${stop_loss}\nTP: ${take_profit}\nSignal: {analysis.get('signal')}\n{regime_msg}"
            logger.info(msg)
            self.telegram.send_alert("Paper Trade Executed 📝", msg, "TRADE")
            
            # Log to DB
            db_entry = {
                "order_id": order_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "side": side,
                "entry_price": current_price,
                "size": quantity,
                "timestamp": position_data["timestamp"],
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "regime": position_data["regime"],
                "market_context": context_extras,
                "analysis_context": analysis,
                "reason": analysis.get("reasoning", "Signal")
            }
            self.db.log_trade_entry(db_entry)
            
            # Log updated report
            self.paper_trading.log_report()
            
        else:
            # Live Trading Execution
            try:
                # We use 'limit' order at current ask price to ensure we don't slip too much, 
                # or 'market' if we want immediate fill. 
                # The prompt asked to use 'Post-Only' if possible/safe, but Imbalance usually implies urgency.
                # However, implementation plan said "Post-Only". Let's try Post-Only Limit near current price.
                
                # Place Order
                order_price = current_price * 0.999 if side == 'buy' else current_price * 1.001 # Slightly better than market?
                # Actually for Post-only buy, price must be < best ask. 
                
                check_spread = self.exchange.check_spread_safety(symbol)
                if not check_spread:
                    logger.warning("Spread too high, aborting execution.")
                    return

                order = self.exchange.place_order(symbol, side, quantity) # Market order for now for reliability of execution in 'Imbalance' context
                
                # Update State
                position_data = {
                    "symbol": symbol,
                    "entry_price": current_price, # approx
                    "size": quantity,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "timestamp": time.time(),
                    "order_id": order.get('id'),
                    "regime": context_extras.get('regime') if context_extras else "UNKNOWN"
                }
                
                self.state.set_position(timeframe, position_data)
                
                # Notify
                regime_msg = f"Regime: {position_data['regime']}"
                msg = f"Opened {timeframe.upper()} position for {symbol}\nSize: {quantity:.4f}\nEntry: {current_price}\nSL: {stop_loss}\nTP: {take_profit}\nSignal: {analysis.get('signal')}\n{regime_msg}"
                logger.info(msg)
                self.telegram.send_alert("Trade Executed 🚀", msg, "TRADE")

                # Log to DB
                db_entry = {
                    "order_id": str(order.get('id')),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": side,
                    "entry_price": current_price,
                    "size": quantity,
                    "timestamp": position_data["timestamp"],
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "regime": position_data["regime"],
                    "market_context": context_extras,
                    "analysis_context": analysis,
                    "reason": analysis.get("reasoning", "Signal")
                }
                self.db.log_trade_entry(db_entry)

            except Exception as e:
                logger.error(f"Execution failed: {e}")
                self.telegram.send_alert("Execution Failed 🚨", f"Failed to execute {symbol}: {e}", "CRITICAL")

    def manage_positions(self):
        """
        Check open positions for SL/TP, Trailing Stops, and Time Exits.
        Supports both paper trading and live trading.
        """
        for tf in ["daily", "weekly"]:
            # Check paper trading position first if enabled
            if Config.PAPER_TRADING and self.paper_trading:
                paper_pos = self.paper_trading.get_position(tf)
                if paper_pos:
                    self._manage_paper_position(tf, paper_pos)
                    continue
            
            pos = self.state.get_position(tf)
            if not pos:
                continue

            symbol = pos["symbol"]
            
            # Fetch current price
            try:
                ticker = self.exchange.get_ticker(symbol)
                current_price = ticker['last']
                
                # 1. Update Highest Price (for Trailing Stop)
                if current_price > pos.get("highest_price", 0):
                    self.state.update_position(tf, {"highest_price": current_price})
                    pos["highest_price"] = current_price # Local update

                # 2. Calculate Stats
                entry_price = pos["entry_price"]
                pnl_percent = (current_price - entry_price) / entry_price * 100
                highest_pnl_percent = (pos["highest_price"] - entry_price) / entry_price * 100
                duration_hours = (time.time() - pos["timestamp"]) / 3600
                
                # 3. Check Stop Loss
                if current_price <= pos["stop_loss"]:
                    logger.info(f"🛑 Stop Loss hit for {symbol} ({tf})")
                    self.exchange.place_order(symbol, "sell", pos["size"]) # Close
                    self.close_position(tf, current_price, "Stop Loss")
                    continue
                    
                # 4. Check Take Profit
                elif current_price >= pos["take_profit"]:
                    logger.info(f"💰 Take Profit hit for {symbol} ({tf})")
                    self.exchange.place_order(symbol, "sell", pos["size"]) # Close
                    self.close_position(tf, current_price, "Take Profit")
                    continue

                # 5. Break-Even Logic
                # Daily: > 1.5% profit -> Move SL to Entry
                # Weekly: > 3.0% profit -> Move SL to Entry
                be_threshold = 1.5 if tf == "daily" else 3.0
                if highest_pnl_percent > be_threshold and pos["stop_loss"] < entry_price:
                    new_sl = entry_price * 1.001 # Slightly above entry to cover fees
                    self.state.update_position(tf, {"stop_loss": new_sl})
                    logger.info(f"🛡️ Moved Stop to Break-Even for {symbol} ({tf})")
                    self.telegram.send_alert("Position Update", f"Moved {symbol} Stop to Break-Even", "INFO")

                # 6. Trailing Stop Logic
                # Daily: > 3% profit -> Trail at -2% from peak
                # Weekly: > 5% profit -> Trail at -3% from peak
                trail_trigger = 3.0 if tf == "daily" else 5.0
                trail_dist = 2.0 if tf == "daily" else 3.0
                
                if highest_pnl_percent > trail_trigger:
                    # Calculate potential new stop
                    trail_price = pos["highest_price"] * (1 - (trail_dist/100))
                    if trail_price > pos["stop_loss"]:
                        self.state.update_position(tf, {"stop_loss": trail_price})
                        logger.info(f"📈 Updated Trailing Stop for {symbol} to {trail_price}")

                # 7. Time-Based Exit
                # Daily: 48h with < 1% profit
                # Weekly: 14 days (336h) with < 2% profit
                if tf == "daily" and duration_hours > 48 and pnl_percent < 1.0:
                    logger.info(f"⏳ Time Exit for {symbol} (Stale)")
                    self.exchange.place_order(symbol, "sell", pos["size"])
                    self.close_position(tf, current_price, "Time Exit (Stale)")
                    continue
                elif tf == "weekly" and duration_hours > 336 and pnl_percent < 2.0:
                    logger.info(f"⏳ Time Exit for {symbol} (Stale)")
                    self.exchange.place_order(symbol, "sell", pos["size"])
                    self.close_position(tf, current_price, "Time Exit (Stale)")
                    continue

            except Exception as e:
                logger.error(f"Error managing position {symbol}: {e}")
    
    def _manage_paper_position(self, tf, pos):
        """Manage a paper trading position."""
        symbol = pos["symbol"]
        
        try:
            ticker = self.exchange.get_ticker(symbol)
            current_price = ticker['last']
            
            # Update highest price in paper trading
            self.paper_trading.update_position_prices(tf, current_price)
            
            # Refresh position after update
            pos = self.paper_trading.get_position(tf)
            if not pos:
                return
            
            # Calculate Stats
            entry_price = pos["entry_price"]
            pnl_percent = (current_price - entry_price) / entry_price * 100
            highest_pnl_percent = (pos.get("highest_price", entry_price) - entry_price) / entry_price * 100
            duration_hours = (time.time() - pos["opened_at"]) / 3600
            
            # Log current position status
            unrealized = self.paper_trading.get_unrealized_pnl(tf, current_price)
            if unrealized:
                logger.info(f"📊 [PAPER] {symbol} ({tf}): Unrealized PnL: ${unrealized['unrealized_pnl']:+.2f} ({unrealized['unrealized_pnl_percent']:+.2f}%)")
            
            # Check Stop Loss
            if current_price <= pos["stop_loss"]:
                logger.info(f"🛑 [PAPER] Stop Loss hit for {symbol} ({tf})")
                self._close_paper_position(tf, current_price, "Stop Loss")
                return
                
            # Check Take Profit
            elif current_price >= pos["take_profit"]:
                logger.info(f"💰 [PAPER] Take Profit hit for {symbol} ({tf})")
                self._close_paper_position(tf, current_price, "Take Profit")
                return

            # Break-Even Logic
            be_threshold = 1.5 if tf == "daily" else 3.0
            if highest_pnl_percent > be_threshold and pos["stop_loss"] < entry_price:
                new_sl = entry_price * 1.001
                # Update paper position stop loss
                self.state.state["paper_trading"]["positions"][tf]["stop_loss"] = new_sl
                self.state.save_state()
                logger.info(f"🛡️ [PAPER] Moved Stop to Break-Even for {symbol} ({tf})")
                self.telegram.send_alert("Paper Position Update", f"Moved {symbol} Stop to Break-Even", "INFO")

            # Trailing Stop Logic
            trail_trigger = 3.0 if tf == "daily" else 5.0
            trail_dist = 2.0 if tf == "daily" else 3.0
            
            if highest_pnl_percent > trail_trigger:
                trail_price = pos["highest_price"] * (1 - (trail_dist/100))
                if trail_price > pos["stop_loss"]:
                    self.state.state["paper_trading"]["positions"][tf]["stop_loss"] = trail_price
                    self.state.save_state()
                    logger.info(f"📈 [PAPER] Updated Trailing Stop for {symbol} to ${trail_price:.2f}")

            # Time-Based Exit
            if tf == "daily" and duration_hours > 48 and pnl_percent < 1.0:
                logger.info(f"⏳ [PAPER] Time Exit for {symbol} (Stale)")
                self._close_paper_position(tf, current_price, "Time Exit (Stale)")
                return
            elif tf == "weekly" and duration_hours > 336 and pnl_percent < 2.0:
                logger.info(f"⏳ [PAPER] Time Exit for {symbol} (Stale)")
                self._close_paper_position(tf, current_price, "Time Exit (Stale)")
                return
                
        except Exception as e:
            logger.error(f"Error managing paper position {symbol}: {e}")
    
    def _close_paper_position(self, timeframe, exit_price, reason):
        """Close a paper trading position."""
        pos = self.paper_trading.get_position(timeframe)
        if not pos:
            return
        
        # Execute paper sell
        result = self.paper_trading.place_order(
            symbol=pos["symbol"],
            side="sell",
            quantity=pos["quantity"],
            price=exit_price,
            timeframe=timeframe
        )
        
        if result.get("success"):
            # Clear state position
            self.state.clear_position(timeframe)
            
            # Get PnL from trade history
            history = self.paper_trading.get_trade_history(limit=1)
            if history:
                last_trade = history[0]
                pnl = last_trade["pnl"]
                pnl_percent = last_trade["pnl_percent"]
                
                # Log to DB
                exit_data = {
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "exit_time": time.time()
                }
                trade_id = pos.get("order_id") or f"PAPER_{time.time()}"
                self.db.update_trade_exit(str(trade_id), exit_data)
                
                msg = f"[PAPER] Closed {timeframe} position: {pos['symbol']}\nReason: {reason}\nPnL: ${pnl:+.2f} ({pnl_percent:+.2f}%)"
                logger.info(msg)
                self.telegram.send_alert("Paper Position Closed", msg, "SUCCESS" if pnl > 0 else "WARNING")
            
            # Log updated report
            self.paper_trading.log_report()

    def close_position(self, timeframe, exit_price, reason):
        pos = self.state.get_position(timeframe)
        if not pos:
            return
            
        entry_price = pos["entry_price"]
        size = pos["size"]
        
        pnl = (exit_price - entry_price) * size
        pnl_percent = (exit_price - entry_price) / entry_price * 100
        
        self.state.record_trade(pnl)
        self.state.clear_position(timeframe)
        
        # Log Exit to DB
        exit_data = {
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl": pnl,
            "pnl_percent": pnl_percent,
            "exit_time": time.time()
        }
        # We need the trade ID. Since we didn't store it in state explicitly for old positions,
        # we might need to handle backward compatibility or just depend on order_id if present.
        # But wait, position_data has 'order_id'.
        trade_id = pos.get('order_id')
        if trade_id:
             self.db.update_trade_exit(str(trade_id), exit_data)
        else:
             logger.warning(f"Could not log exit for {pos['symbol']} - No Order ID found.")
        
        msg = f"Closed {timeframe} position: {pos['symbol']}\nReason: {reason}\nPnL: ${pnl:.2f} ({pnl_percent:.2f}%)"
        self.telegram.send_alert("Position Closed", msg, "SUCCESS" if pnl > 0 else "WARNING")
