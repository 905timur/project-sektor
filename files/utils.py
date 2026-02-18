"""
Shared utility functions for the trading bot.
"""
from datetime import datetime
from zoneinfo import ZoneInfo


def get_trading_session():
    """
    Determine current trading session based on ET time.
    
    Session times in Eastern Time:
    - Asia: 8 PM - 5 AM ET (Tokyo, Sydney)
    - London: 3 AM - 12 PM ET
    - New York: 8 AM - 5 PM ET
    - London/NY Overlap: 8 AM - 12 PM ET
    """
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    hour = now.hour
    
    if 20 <= hour or hour < 3:
        return "🌏 ASIA"
    elif 3 <= hour < 8:
        return "🇬🇧 LONDON"
    elif 8 <= hour < 13:
        return "🇬🇧🇺🇸 LONDON/NY OVERLAP"
    elif 13 <= hour < 17:
        return "🇺🇸 NEW YORK"
    else:
        return "🇺🇸 NEW YORK (AFTERNOON)"
