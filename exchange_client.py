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
