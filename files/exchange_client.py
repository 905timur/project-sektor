import ccxt.pro as ccxt  # Use Pro for potential WS support later, though using getting started with REST
import ccxt
import time
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from config import Config

logger = logging.getLogger(__name__)

class ExchangeClient:
    def __init__(self):
        self.client = ccxt.cryptocom({
            'apiKey': Config.CRYPTO_COM_API_KEY,
            'secret': Config.CRYPTO_COM_API_SECRET,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })
        
        if Config.PAPER_TRADING:
            logger.info("⚠️ PAPER TRADING MODE ENABLED ⚠️ - No real orders will be placed via API")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_ticker(self, symbol):
        try:
            return self.client.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_orderbook(self, symbol, limit=10):
        try:
            return self.client.fetch_order_book(symbol, limit)
        except Exception as e:
            logger.error(f"Error fetching orderbook for {symbol}: {e}")
            raise

    def check_spread_safety(self, symbol):
        """
        Ensures bid-ask spread is within safe limits.
        """
        try:
            orderbook = self.get_orderbook(symbol)
            bid = orderbook['bids'][0][0] if orderbook['bids'] else 0
            ask = orderbook['asks'][0][0] if orderbook['asks'] else 0
            
            if bid == 0:
                logger.warning(f"No bids for {symbol}")
                return False

            spread_percent = ((ask - bid) / bid) * 100
            
            if spread_percent > Config.SPREAD_LIMIT_PERCENT:
                logger.warning(f"Spread Risk: {symbol} spread {spread_percent:.3f}% > Limit {Config.SPREAD_LIMIT_PERCENT}%")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Spread check failed: {e}")
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_orderbook_imbalance(self, symbol):
        """
        Compute multi-level order book imbalance metrics.
        Returns dict with top1/top5/top10 imbalance ratios, micro-price, and label.
        Returns None on error.
        """
        try:
            orderbook = self.get_orderbook(symbol, limit=20)
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])

            # Ensure we have enough levels
            if not bids or not asks or len(bids) < 1 or len(asks) < 1:
                logger.warning(f"Insufficient order book data for {symbol}")
                return None

            # Top-1 imbalance (best bid vs best ask volume)
            top1_imbalance = round(bids[0][1] / asks[0][1], 2) if asks[0][1] > 0 else None

            # Top-5 imbalance
            bid_vol_5 = sum(b[1] for b in bids[:5])
            ask_vol_5 = sum(a[1] for a in asks[:5])
            top5_imbalance = round(bid_vol_5 / ask_vol_5, 2) if ask_vol_5 > 0 else None

            # Top-10 imbalance
            bid_vol_10 = sum(b[1] for b in bids[:10])
            ask_vol_10 = sum(a[1] for a in asks[:10])
            top10_imbalance = round(bid_vol_10 / ask_vol_10, 2) if ask_vol_10 > 0 else None

            # Micro-price (volume-weighted mid)
            best_bid_price = bids[0][0]
            best_ask_price = asks[0][0]
            bid_volume = bids[0][1]
            ask_volume = asks[0][1]
            total_volume = bid_volume + ask_volume
            micro_price = round((best_bid_price * ask_volume + best_ask_price * bid_volume) / total_volume, 2) if total_volume > 0 else None

            # Determine label based on top5_imbalance
            if top5_imbalance is None:
                label = "Balanced"
            elif top5_imbalance >= 1.5:
                label = "Strong Bid Pressure"
            elif top5_imbalance >= 1.15:
                label = "Mild Bid Pressure"
            elif top5_imbalance <= 0.67:
                label = "Strong Ask Pressure"
            elif top5_imbalance <= 0.87:
                label = "Mild Ask Pressure"
            else:
                label = "Balanced"

            return {
                "top1_imbalance": top1_imbalance,
                "top5_imbalance": top5_imbalance,
                "top10_imbalance": top10_imbalance,
                "micro_price": micro_price,
                "label": label
            }
        except Exception as e:
            logger.warning(f"Error computing order book imbalance for {symbol}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        try:
            return self.client.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} {timeframe}: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_balance(self, currency="USDT"):
        try:
            balance = self.client.fetch_balance()
            return balance[currency]['free']
        except Exception as e:
            logger.error(f"Error fetching balance for {currency}: {e}")
            raise

    def place_order(self, symbol, side, amount, price=None, params={}):
        """
        Place an order. Respects PAPER_TRADING.
        Uses 'postOnly' if specified in params to ensure maker fees.
        """
        if Config.PAPER_TRADING:
            logger.info(f"PAPER TRADE: {side.upper()} {amount} {symbol} @ {price or 'MARKET'}")
            return {'id': f'paper_{int(time.time())}', 'status': 'open', 'filled': amount, 'average': price, 'datetime': ccxt.iso8601(time.time()*1000)}

        # Real Order Logic
        try:
            if price:
                return self.client.create_order(symbol, 'limit', side, amount, price, params)
            else:
                return self.client.create_order(symbol, 'market', side, amount, params)
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def close_connection(self):
        # CCXT doesn't strictly require close for REST, but good for cleanup if using async
        pass
