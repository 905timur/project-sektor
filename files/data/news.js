#!/usr/bin/env node

/**
 * Multi-Source Crypto RSS News Client
 * 100% FREE - No API keys, no rate limits
 * 
 * Aggregates from:
 * - CoinTelegraph, CoinDesk, Decrypt, The Block
 * - Bitcoin Magazine, NewsBTC, CryptoPotato
 * 
 * Includes sentiment analysis and age categorization for bot compatibility.
 */

const https = require('https');

const RSS_SOURCES = {
  cointelegraph: {
    url: 'cointelegraph.com/rss',
    name: 'CoinTelegraph',
    enabled: true
  },
  coindesk: {
    url: 'www.coindesk.com/arc/outboundfeeds/rss/',
    name: 'CoinDesk',
    enabled: true
  },
  decrypt: {
    url: 'decrypt.co/feed',
    name: 'Decrypt',
    enabled: true
  },
  theblock: {
    url: 'www.theblock.co/rss.xml',
    name: 'The Block',
    enabled: true
  },
  bitcoinmagazine: {
    url: 'bitcoinmagazine.com/.rss/full/',
    name: 'Bitcoin Magazine',
    enabled: true
  },
  newsbtc: {
    url: 'www.newsbtc.com/feed/',
    name: 'NewsBTC',
    enabled: true
  },
  cryptopotato: {
    url: 'cryptopotato.com/feed/',
    name: 'CryptoPotato',
    enabled: true
  }
};

class RSSNewsClient {
  constructor(options = {}) {
    this.cache = {};
    this.cacheExpiry = options.DEFAULT_CACHE_MS || 5 * 60 * 1000;
    this.breakingNewsAgeMs = options.BREAKING_NEWS_AGE_MS || 2 * 60 * 60 * 1000;
  }

  /**
   * Fetch RSS feed from URL
   * @private
   */
  async fetchRSS(url) {
    return new Promise((resolve, reject) => {
      const parts = url.split('/');
      const options = {
        hostname: parts[0],
        path: '/' + parts.slice(1).join('/'),
        method: 'GET',
        timeout: 10000,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
          'Accept': 'application/xml, text/xml, */*'
        }
      };

      const req = https.get(options, (res) => {
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }

        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => resolve(data));
      });
      
      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Timeout'));
      });
    });
  }

  /**
   * Parse RSS XML to articles
   * @private
   */
  parseRSS(xml, sourceName) {
    const articles = [];
    const itemRegex = /<item>([\s\S]*?)<\/item>|<entry>([\s\S]*?)<\/entry>/g;
    
    let match;
    while ((match = itemRegex.exec(xml)) !== null) {
      const itemXml = match[1] || match[2];
      
      const getField = (field, alternatives = []) => {
        const fields = [field, ...alternatives];
        for (const f of fields) {
          const cdataMatch = itemXml.match(new RegExp(`<${f}><!\\[CDATA\\[(.*?)\\]\\]></${f}>`, 's'));
          if (cdataMatch) return cdataMatch[1].trim();
          
          const normalMatch = itemXml.match(new RegExp(`<${f}[^>]*>(.*?)</${f}>`, 's'));
          if (normalMatch) return normalMatch[1].replace(/<\/?[^>]+(>|$)/g, "").trim();
        }
        return '';
      };
      
      const title = getField('title');
      const link = getField('link', ['guid']);
      const pubDate = getField('pubDate', ['published', 'updated']);
      const description = getField('description', ['summary', 'content:encoded']);
      
      if (title && link) {
        articles.push({
          title,
          link: link.replace(/<!\[CDATA\[(.*?)\]\]>/, '$1').trim(),
          pubDate,
          description: description.substring(0, 500),
          source: sourceName
        });
      }
    }
    
    return articles;
  }

  /**
   * Fetch from single RSS source
   * @private
   */
  async fetchSource(sourceKey) {
    const source = RSS_SOURCES[sourceKey];
    if (!source.enabled) return [];

    try {
      const xml = await this.fetchRSS(source.url);
      return this.parseRSS(xml, source.name);
    } catch (error) {
      console.error(`[${source.name}] Failed: ${error.message}`);
      return [];
    }
  }

  /**
   * Fetch from all enabled RSS sources
   */
  async getAllNews(limit = 10) {
    const cacheKey = `all_${limit}`;
    const now = Date.now();
    
    if (this.cache[cacheKey] && now - this.cache[cacheKey].timestamp < this.cacheExpiry) {
      return this.cache[cacheKey].data;
    }

    const sourceKeys = Object.keys(RSS_SOURCES).filter(k => RSS_SOURCES[k].enabled);
    const results = await Promise.all(sourceKeys.map(key => this.fetchSource(key)));
    
    let allArticles = results.flat();
    
    allArticles.sort((a, b) => {
      const dateA = new Date(a.pubDate || 0);
      const dateB = new Date(b.pubDate || 0);
      return dateB - dateA;
    });
    
    const deduplicated = this.deduplicateArticles(allArticles);
    const finalArticles = deduplicated.slice(0, limit * sourceKeys.length);
    
    const result = {
      articles: finalArticles,
      totalCount: finalArticles.length,
      fetchedAt: new Date().toISOString()
    };
    
    this.cache[cacheKey] = { data: result, timestamp: now };
    return result;
  }

  // Alias for trader-bot compatibility
  async getLatestNews(limit = 10) {
    return this.getAllNews(limit);
  }

  /**
   * Deduplicate by title similarity
   * @private
   */
  deduplicateArticles(articles) {
    const unique = [];
    for (const article of articles) {
      const isDuplicate = unique.some(existing => this.titleSimilarity(article.title, existing.title) > 0.8);
      if (!isDuplicate) unique.push(article);
    }
    return unique;
  }

  /**
   * Calculate title similarity (Jaccard)
   * @private
   */
  titleSimilarity(title1, title2) {
    const normalize = (str) => str.toLowerCase().replace(/[^\w\s]/g, '').split(/\s+/).filter(w => w.length > 3);
    const words1 = new Set(normalize(title1));
    const words2 = new Set(normalize(title2));
    if (words1.size === 0 || words2.size === 0) return 0;
    const intersection = new Set([...words1].filter(w => words2.has(w)));
    const union = new Set([...words1, ...words2]);
    return intersection.size / union.size;
  }

  /**
   * Get Bitcoin-specific news
   */
  async getBitcoinNews(limit = 10) {
    const all = await this.getAllNews(50);
    const keywords = ['bitcoin', 'btc', 'satoshi'];
    const filtered = all.articles.filter(article => {
      const text = `${article.title} ${article.description}`.toLowerCase();
      return keywords.some(kw => text.includes(kw));
    });
    return { articles: filtered.slice(0, limit), totalCount: filtered.length, fetchedAt: new Date().toISOString() };
  }

  /**
   * Get DeFi-specific news
   */
  async getDefiNews(limit = 10) {
    const all = await this.getAllNews(50);
    const keywords = ['defi', 'decentralized finance', 'dex', 'uniswap', 'aave', 'compound'];
    const filtered = all.articles.filter(article => {
      const text = `${article.title} ${article.description}`.toLowerCase();
      return keywords.some(kw => text.includes(kw));
    });
    return { articles: filtered.slice(0, limit), totalCount: filtered.length, fetchedAt: new Date().toISOString() };
  }

  /**
   * Get breaking news
   */
  async getBreakingNews(limit = 10) {
    const all = await this.getAllNews(50);
    const now = Date.now();
    const filtered = all.articles.filter(article => {
      const pubDate = new Date(article.pubDate || 0);
      return (now - pubDate.getTime()) < this.breakingNewsAgeMs;
    });
    return { articles: filtered.slice(0, limit), totalCount: filtered.length, fetchedAt: new Date().toISOString() };
  }

  /**
   * Search news
   */
  async searchNews(query, limit = 10) {
    const all = await this.getAllNews(50);
    const keywords = query.toLowerCase().split(/[,\s]+/).filter(k => k.length > 2);
    const filtered = all.articles.filter(article => {
      const text = `${article.title} ${article.description}`.toLowerCase();
      return keywords.some(kw => text.includes(kw));
    });
    return { articles: filtered.slice(0, limit), totalCount: filtered.length, fetchedAt: new Date().toISOString() };
  }

  /**
   * Analyze sentiment
   */
  analyzeSentiment(articles) {
    let bullishCount = 0;
    let bearishCount = 0;
    let neutralCount = 0;

    const bullishKeywords = ['surge', 'rally', 'bullish', 'breakout', 'gains', 'soar', 'pump', 'moon', 'adoption', 'breakthrough', 'positive', 'upgrade', 'partnership', 'institutional', 'accumulation'];
    const bearishKeywords = ['crash', 'plunge', 'bearish', 'dump', 'decline', 'drop', 'sell-off', 'correction', 'fear', 'regulation', 'hack', 'scam', 'fraud', 'ban', 'negative', 'warning'];

    articles.forEach(article => {
      const text = `${article.title} ${article.description}`.toLowerCase();
      const bullishMatches = bullishKeywords.filter(kw => text.includes(kw)).length;
      const bearishMatches = bearishKeywords.filter(kw => text.includes(kw)).length;

      if (bullishMatches > bearishMatches) bullishCount++;
      else if (bearishMatches > bullishMatches) bearishCount++;
      else neutralCount++;
    });

    const total = articles.length;
    return {
      overallSentiment: bullishCount > bearishCount ? 'BULLISH' : (bearishCount > bullishCount ? 'BEARISH' : 'NEUTRAL'),
      confidence: total > 0 ? (Math.max(bullishCount, bearishCount) / total > 0.6 ? 'HIGH' : 'MEDIUM') : 'LOW',
      bullishPercent: total > 0 ? Math.round((bullishCount/total)*100) : 0,
      bearishPercent: total > 0 ? Math.round((bearishCount/total)*100) : 0,
      neutralPercent: total > 0 ? Math.round((neutralCount/total)*100) : 0,
      articleCount: total,
      breakdown: { bullish: bullishCount, bearish: bearishCount, neutral: neutralCount }
    };
  }

  /**
   * Categorize by age
   */
  categorizeByAge(articles) {
    const now = Date.now();
    const breaking = [];
    const regular = [];
    for (const article of articles) {
      const pubDate = new Date(article.pubDate || 0);
      if ((now - pubDate.getTime()) < this.breakingNewsAgeMs) {
        breaking.push({ ...article, _isBreaking: true });
      } else {
        regular.push({ ...article, _isBreaking: false });
      }
    }
    return { breaking, regular };
  }

  /**
   * Health check
   */
  async getHealth() {
    try {
      const news = await this.getAllNews(1);
      return { status: news.articles.length > 0 ? 'healthy' : 'degraded', source: 'RSS Aggregator', articleCount: news.articles.length };
    } catch (e) {
      return { status: 'unhealthy', error: e.message };
    }
  }
}

module.exports = RSSNewsClient;

// CLI usage
if (require.main === module) {
  const client = new RSSNewsClient();
  const command = process.argv[2];
  const arg = process.argv[3];

  (async () => {
    try {
      switch (command) {
        case 'all':
          console.log(JSON.stringify(await client.getAllNews(parseInt(arg) || 20), null, 2));
          break;
        case 'bitcoin':
          console.log(JSON.stringify(await client.getBitcoinNews(parseInt(arg) || 10), null, 2));
          break;
        case 'health':
          console.log(JSON.stringify(await client.getHealth(), null, 2));
          break;
        case 'sentiment':
          const news = await client.getAllNews(10);
          console.log(JSON.stringify(client.analyzeSentiment(news.articles), null, 2));
          break;
        default:
          console.log('Usage: node rss-news-client.js <all|bitcoin|health|sentiment> [limit]');
          break;
      }
    } catch (error) {
      console.error('Error:', error.message);
      process.exit(1);
    }
  })();
}
