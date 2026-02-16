import logging
import time
from config import Config

logger = logging.getLogger(__name__)

class OpportunityTracker:
    def __init__(self, state_manager):
        self.state = state_manager
        # "watching" dict structure:
        # {
        #   "BTC/USDT": {
        #       "symbol": "BTC/USDT",
        #       "timeframe": "daily",
        #       "imbalance_type": "fvg", # or 'ob'
        #       "zone_top": 50000,
        #       "zone_bottom": 49000,
        #       "bias": "bullish",
        #       "created_at": timestamp,
        #       "stage": "watching" # watching -> retraced -> analyzing
        #   }
        # }
        self.watching = self.state.get_watching_opportunities()

    def add_opportunity(self, symbol, timeframe, zone_data):
        """
        Adds a new opportunity to the watch list.
        zone_data: {'type', 'top', 'bottom', 'created_at_price'}
        """
        key = f"{symbol}_{timeframe}"
        
        # Don't overwrite if we are already watching/trading this
        if key in self.watching:
            return

        opportunity = {
            "id": f"{symbol}_{int(time.time())}",
            "symbol": symbol,
            "timeframe": timeframe,
            "imbalance_type": zone_data['type'],
            "zone_top": zone_data['top'],
            "zone_bottom": zone_data['bottom'],
            "bias": "bullish" if "bullish" in zone_data['type'] else "bearish",
            "created_at": time.time(),
            "stage": "watching",
            "last_price": zone_data['created_at_price']
        }
        
        self.watching[key] = opportunity
        self.state.save_watching_opportunities(self.watching)
        logger.info(f"👀 Added to Watchlist: {symbol} ({timeframe}) - Zone: {opportunity['zone_bottom']}-{opportunity['zone_top']}")

    def check_retracement(self, symbol, timeframe, current_tick):
        """
        Checks if price has retraced into the imbalance zone.
        Returns True if ready for Analysis.
        """
        key = f"{symbol}_{timeframe}"
        opp = self.watching.get(key)
        
        if not opp:
            return False
            
        current_price = current_tick['close']
        
        # Bullish Setup: Waiting for price to drop INTO zone
        if opp['bias'] == 'bullish':
            # Check for invalidation first (price dropped BELOW zone)
            if current_price < opp['zone_bottom']:
                logger.info(f"❌ Opportunity Invalidated (Price below zone): {symbol}")
                self.remove_opportunity(symbol, timeframe)
                return False
                
            # Check for retracement (Price <= Top AND Price >= Bottom)
            # Actually we want it to dip INTO the zone.
            if current_price <= opp['zone_top']:
                # Calculate depth
                zone_range = opp['zone_top'] - opp['zone_bottom']
                penetration = (opp['zone_top'] - current_price) / zone_range
                
                # We want at least some penetration, e.g. top 10% of zone cleared
                # Config might have specific threshold
                logger.info(f"🎯 Retracement Detected: {symbol} inside zone (Depth: {penetration:.0%})")
                return True
                
        # Bearish Setup: Waiting for price to rise INTO zone
        elif opp['bias'] == 'bearish':
             # Check for invalidation (price rose ABOVE zone)
            if current_price > opp['zone_top']:
                logger.info(f"❌ Opportunity Invalidated (Price above zone): {symbol}")
                self.remove_opportunity(symbol, timeframe)
                return False
                
            # Check for retracement (Price >= Bottom AND Price <= Top)
            if current_price >= opp['zone_bottom']:
                logger.info(f"🎯 Retracement Detected: {symbol} inside zone")
                return True
                
        return False

    def remove_opportunity(self, symbol, timeframe):
        key = f"{symbol}_{timeframe}"
        if key in self.watching:
            del self.watching[key]
            self.state.save_watching_opportunities(self.watching)

    def get_watch_list(self):
        return self.watching
