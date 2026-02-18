#!/usr/bin/env python3
"""
Multi-Source Crypto RSS News Client
100% FREE - No API keys, no rate limits

Aggregates from:
- CoinTelegraph, CoinDesk, Decrypt, The Block
- Bitcoin Magazine, NewsBTC, CryptoPotato

Includes sentiment analysis and age categorization for bot compatibility.
Ported from Node.js reference implementation.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from urllib import request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

# RSS Sources Configuration
RSS_SOURCES = {
    'cointelegraph': {
        'url': 'cointelegraph.com/rss',
        'name': 'CoinTelegraph',
        'enabled': True
    },
    'coindesk': {
        'url': 'www.coindesk.com/arc/outboundfeeds/rss/',
        'name': 'CoinDesk',
        'enabled': True
    },
    'decrypt': {
        'url': 'decrypt.co/feed',
        'name': 'Decrypt',
        'enabled': True
    },
    'theblock': {
        'url': 'www.theblock.co/rss.xml',
        'name': 'The Block',
        'enabled': True
    },
    'bitcoinmagazine': {
        'url': 'bitcoinmagazine.com/.rss/full/',
        'name': 'Bitcoin Magazine',
        'enabled': True
    },
    'newsbtc': {
        'url': 'www.newsbtc.com/feed/',
        'name': 'NewsBTC',
        'enabled': True
    },
    'cryptopotato': {
        'url': 'cryptopotato.com/feed/',
        'name': 'CryptoPotato',
        'enabled': True
    }
}


class RSSNewsClient:
    """
    RSS News Client for fetching and analyzing crypto news.
    
    Features:
    - Fetches from 7 major crypto news sources
    - Deduplicates articles by title similarity (Jaccard)
    - Analyzes sentiment (bullish/bearish keywords)
    - In-memory cache with 5-minute expiry
    """
    
    def __init__(
        self,
        cache_expiry_ms: int = 5 * 60 * 1000,
        breaking_news_age_ms: int = 2 * 60 * 60 * 1000,
        source_timeout: int = 10
    ):
        """
        Initialize the RSS News Client.
        
        Args:
            cache_expiry_ms: Cache expiry time in milliseconds (default: 5 minutes)
            breaking_news_age_ms: Age threshold for breaking news in milliseconds (default: 2 hours)
            source_timeout: Timeout per source in seconds (default: 10)
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry_ms = cache_expiry_ms
        self._breaking_news_age_ms = breaking_news_age_ms
        self._source_timeout = source_timeout
    
    def _fetch_rss(self, url: str) -> str:
        """
        Fetch RSS feed from URL.
        
        Args:
            url: RSS feed URL (without https://)
            
        Returns:
            XML string content
            
        Raises:
            Exception on failure (caught by caller)
        """
        full_url = f"https://{url}"
        
        # Create request with headers
        req = request.Request(
            full_url,
            method='GET',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/xml, text/xml, */*'
            }
        )
        
        try:
            with request.urlopen(req, timeout=self._source_timeout) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                return response.read().decode('utf-8', errors='replace')
        except HTTPError as e:
            raise Exception(f"HTTP {e.code}")
        except URLError as e:
            raise Exception(f"URL Error: {e.reason}")
        except Exception as e:
            raise Exception(str(e))
    
    def _parse_rss(self, xml: str, source_name: str) -> List[Dict[str, str]]:
        """
        Parse RSS XML to articles.
        
        Handles both <item> and <entry> tags, and CDATA sections.
        
        Args:
            xml: RSS XML string
            source_name: Name of the source for article metadata
            
        Returns:
            List of article dictionaries
        """
        articles = []
        
        # Match both <item> and <entry> tags
        item_pattern = r'<item>([\s\S]*?)</item>|<entry>([\s\S]*?)</entry>'
        
        for match in re.finditer(item_pattern, xml):
            item_xml = match.group(1) or match.group(2)
            
            def get_field(field: str, alternatives: List[str] = None) -> str:
                """Extract field from item XML, handling CDATA."""
                if alternatives is None:
                    alternatives = []
                
                fields = [field] + alternatives
                
                for f in fields:
                    # Try CDATA match first
                    cdata_pattern = rf'<{f}><!\[CDATA\[(.*?)\]\]></{f}>'
                    cdata_match = re.search(cdata_pattern, item_xml, re.DOTALL)
                    if cdata_match:
                        return cdata_match.group(1).strip()
                    
                    # Try normal match
                    normal_pattern = rf'<{f}[^>]*>(.*?)</{f}>'
                    normal_match = re.search(normal_pattern, item_xml, re.DOTALL)
                    if normal_match:
                        # Remove any nested HTML tags
                        content = re.sub(r'</?[^>]+(>|$)', '', normal_match.group(1))
                        return content.strip()
                
                return ''
            
            title = get_field('title')
            link = get_field('link', ['guid'])
            pub_date = get_field('pubDate', ['published', 'updated'])
            description = get_field('description', ['summary', 'content:encoded'])
            
            # Clean CDATA from link if present
            link = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', link).strip()
            
            if title and link:
                articles.append({
                    'title': title,
                    'link': link,
                    'pubDate': pub_date,
                    'description': description[:500],  # Limit description length
                    'source': source_name
                })
        
        return articles
    
    def _fetch_source(self, source_key: str) -> List[Dict[str, str]]:
        """
        Fetch from single RSS source.
        
        Args:
            source_key: Key from RSS_SOURCES dict
            
        Returns:
            List of articles (empty list on failure)
        """
        source = RSS_SOURCES.get(source_key)
        if not source or not source.get('enabled', True):
            return []
        
        try:
            xml = self._fetch_rss(source['url'])
            return self._parse_rss(xml, source['name'])
        except Exception as e:
            logger.warning(f"[{source['name']}] Failed: {e}")
            return []
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        Calculate title similarity using Jaccard index.
        
        Args:
            title1: First title
            title2: Second title
            
        Returns:
            Similarity score between 0 and 1
        """
        def normalize(text: str) -> set:
            """Normalize text to set of significant words."""
            words = re.sub(r'[^\w\s]', '', text.lower()).split()
            return set(w for w in words if len(w) > 3)
        
        words1 = normalize(title1)
        words2 = normalize(title2)
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union)
    
    def _deduplicate_articles(self, articles: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Deduplicate articles by title similarity.
        
        Uses Jaccard similarity with 0.8 threshold.
        
        Args:
            articles: List of articles
            
        Returns:
            Deduplicated list of articles
        """
        unique = []
        
        for article in articles:
            is_duplicate = any(
                self._title_similarity(article['title'], existing['title']) > 0.8
                for existing in unique
            )
            if not is_duplicate:
                unique.append(article)
        
        return unique
    
    def _parse_date(self, date_str: str) -> datetime:
        """
        Parse RSS date string to datetime.
        
        Args:
            date_str: Date string from RSS feed
            
        Returns:
            datetime object (or epoch if parsing fails)
        """
        if not date_str:
            return datetime.fromtimestamp(0, tz=timezone.utc)
        
        # Common RSS date formats
        formats = [
            '%a, %d %b %Y %H:%M:%S %z',
            '%a, %d %b %Y %H:%M:%S GMT',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%SZ',
            '%a, %d %b %Y %H:%M:%S',
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        
        # Fallback to epoch
        return datetime.fromtimestamp(0, tz=timezone.utc)
    
    def get_all_news(self, limit: int = 10) -> Dict[str, Any]:
        """
        Fetch from all enabled RSS sources.
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            Dict with articles, totalCount, and fetchedAt
        """
        cache_key = f"all_{limit}"
        now = time.time() * 1000  # Current time in ms
        
        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if now - cached['timestamp'] < self._cache_expiry_ms:
                return cached['data']
        
        # Get enabled source keys
        source_keys = [k for k, v in RSS_SOURCES.items() if v.get('enabled', True)]
        
        # Fetch all sources concurrently
        all_articles = []
        with ThreadPoolExecutor(max_workers=len(source_keys)) as executor:
            futures = {executor.submit(self._fetch_source, key): key for key in source_keys}
            
            for future in futures:
                try:
                    articles = future.result(timeout=self._source_timeout)
                    all_articles.extend(articles)
                except FuturesTimeoutError:
                    logger.warning(f"Source fetch timed out")
                except Exception as e:
                    logger.warning(f"Source fetch failed: {e}")
        
        # Sort by publication date (newest first)
        all_articles.sort(
            key=lambda a: self._parse_date(a.get('pubDate', '')),
            reverse=True
        )
        
        # Deduplicate
        deduplicated = self._deduplicate_articles(all_articles)
        
        # Limit results
        final_articles = deduplicated[:limit * len(source_keys)]
        
        result = {
            'articles': final_articles,
            'totalCount': len(final_articles),
            'fetchedAt': datetime.now(timezone.utc).isoformat()
        }
        
        # Cache result
        self._cache[cache_key] = {
            'data': result,
            'timestamp': now
        }
        
        return result
    
    def get_latest_news(self, limit: int = 10) -> Dict[str, Any]:
        """Alias for trader-bot compatibility."""
        return self.get_all_news(limit)
    
    def get_cached_news(self, symbol: str = None) -> Optional[Dict[str, Any]]:
        """
        Return cached news without making a new HTTP fetch.
        This is called from the hot path of manage_positions and must never block.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT') - used to determine if Bitcoin-specific news
            
        Returns:
            Cached news dict or None if no cache entry exists or it is expired
        """
        now = time.time() * 1000  # Current time in ms
        
        # Determine which cache key to check based on symbol
        if symbol and 'BTC' in symbol.upper():
            # Check for Bitcoin-specific cache first
            cache_key = "all_50"  # get_bitcoin_news uses get_all_news(50)
        else:
            cache_key = "all_10"
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if now - cached['timestamp'] < self._cache_expiry_ms:
                return cached['data']
        
        return None
    
    def get_bitcoin_news(self, limit: int = 10) -> Dict[str, Any]:
        """
        Get Bitcoin-specific news.
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            Dict with filtered articles
        """
        all_news = self.get_all_news(50)
        keywords = ['bitcoin', 'btc', 'satoshi']
        
        filtered = [
            article for article in all_news['articles']
            if any(kw in f"{article['title']} {article.get('description', '')}".lower() for kw in keywords)
        ]
        
        return {
            'articles': filtered[:limit],
            'totalCount': len(filtered),
            'fetchedAt': datetime.now(timezone.utc).isoformat()
        }
    
    def get_defi_news(self, limit: int = 10) -> Dict[str, Any]:
        """
        Get DeFi-specific news.
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            Dict with filtered articles
        """
        all_news = self.get_all_news(50)
        keywords = ['defi', 'decentralized finance', 'dex', 'uniswap', 'aave', 'compound']
        
        filtered = [
            article for article in all_news['articles']
            if any(kw in f"{article['title']} {article.get('description', '')}".lower() for kw in keywords)
        ]
        
        return {
            'articles': filtered[:limit],
            'totalCount': len(filtered),
            'fetchedAt': datetime.now(timezone.utc).isoformat()
        }
    
    def get_breaking_news(self, limit: int = 10) -> Dict[str, Any]:
        """
        Get breaking news (published within last 2 hours).
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            Dict with filtered articles
        """
        all_news = self.get_all_news(50)
        now_ms = time.time() * 1000
        
        filtered = []
        for article in all_news['articles']:
            pub_date = self._parse_date(article.get('pubDate', ''))
            pub_ms = pub_date.timestamp() * 1000
            
            if (now_ms - pub_ms) < self._breaking_news_age_ms:
                filtered.append(article)
        
        return {
            'articles': filtered[:limit],
            'totalCount': len(filtered),
            'fetchedAt': datetime.now(timezone.utc).isoformat()
        }
    
    def search_news(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """
        Search news by query.
        
        Args:
            query: Search query
            limit: Maximum number of articles to return
            
        Returns:
            Dict with filtered articles
        """
        all_news = self.get_all_news(50)
        keywords = [k for k in query.lower().split() if len(k) > 2]
        
        filtered = [
            article for article in all_news['articles']
            if any(kw in f"{article['title']} {article.get('description', '')}".lower() for kw in keywords)
        ]
        
        return {
            'articles': filtered[:limit],
            'totalCount': len(filtered),
            'fetchedAt': datetime.now(timezone.utc).isoformat()
        }
    
    def analyze_sentiment(self, articles: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Analyze sentiment of articles.
        
        Uses keyword matching for bullish/bearish sentiment.
        
        Args:
            articles: List of article dictionaries
            
        Returns:
            Dict with sentiment analysis results
        """
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        
        bullish_keywords = [
            'surge', 'rally', 'bullish', 'breakout', 'gains', 'soar', 'pump',
            'moon', 'adoption', 'breakthrough', 'positive', 'upgrade',
            'partnership', 'institutional', 'accumulation'
        ]
        
        bearish_keywords = [
            'crash', 'plunge', 'bearish', 'dump', 'decline', 'drop',
            'sell-off', 'correction', 'fear', 'regulation', 'hack',
            'scam', 'fraud', 'ban', 'negative', 'warning'
        ]
        
        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            
            bullish_matches = sum(1 for kw in bullish_keywords if kw in text)
            bearish_matches = sum(1 for kw in bearish_keywords if kw in text)
            
            if bullish_matches > bearish_matches:
                bullish_count += 1
            elif bearish_matches > bullish_matches:
                bearish_count += 1
            else:
                neutral_count += 1
        
        total = len(articles)
        
        if total == 0:
            return {
                'overallSentiment': 'NEUTRAL',
                'confidence': 'LOW',
                'bullishPercent': 0,
                'bearishPercent': 0,
                'neutralPercent': 0,
                'articleCount': 0,
                'breakdown': {'bullish': 0, 'bearish': 0, 'neutral': 0}
            }
        
        # Determine overall sentiment
        if bullish_count > bearish_count:
            overall = 'BULLISH'
        elif bearish_count > bullish_count:
            overall = 'BEARISH'
        else:
            overall = 'NEUTRAL'
        
        # Determine confidence
        max_count = max(bullish_count, bearish_count)
        confidence = 'HIGH' if (max_count / total) > 0.6 else 'MEDIUM'
        
        return {
            'overallSentiment': overall,
            'confidence': confidence,
            'bullishPercent': round((bullish_count / total) * 100),
            'bearishPercent': round((bearish_count / total) * 100),
            'neutralPercent': round((neutral_count / total) * 100),
            'articleCount': total,
            'breakdown': {
                'bullish': bullish_count,
                'bearish': bearish_count,
                'neutral': neutral_count
            }
        }
    
    def categorize_by_age(self, articles: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
        """
        Categorize articles by age.
        
        Args:
            articles: List of article dictionaries
            
        Returns:
            Dict with 'breaking' and 'regular' lists
        """
        now_ms = time.time() * 1000
        breaking = []
        regular = []
        
        for article in articles:
            pub_date = self._parse_date(article.get('pubDate', ''))
            pub_ms = pub_date.timestamp() * 1000
            
            if (now_ms - pub_ms) < self._breaking_news_age_ms:
                breaking.append({**article, '_isBreaking': True})
            else:
                regular.append({**article, '_isBreaking': False})
        
        return {'breaking': breaking, 'regular': regular}
    
    def get_health(self) -> Dict[str, Any]:
        """
        Health check for the news client.
        
        Returns:
            Dict with health status
        """
        try:
            news = self.get_all_news(1)
            return {
                'status': 'healthy' if news['articles'] else 'degraded',
                'source': 'RSS Aggregator',
                'articleCount': news['totalCount']
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e)
            }


# CLI usage
if __name__ == '__main__':
    import sys
    import json
    
    client = RSSNewsClient()
    
    if len(sys.argv) < 2:
        print("Usage: python news_client.py <all|bitcoin|breaking|health|sentiment> [limit]")
        sys.exit(1)
    
    command = sys.argv[1]
    arg = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    try:
        if command == 'all':
            result = client.get_all_news(arg)
        elif command == 'bitcoin':
            result = client.get_bitcoin_news(arg)
        elif command == 'breaking':
            result = client.get_breaking_news(arg)
        elif command == 'health':
            result = client.get_health()
        elif command == 'sentiment':
            news = client.get_all_news(arg)
            result = client.analyze_sentiment(news['articles'])
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
        
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
