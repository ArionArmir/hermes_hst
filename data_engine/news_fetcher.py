import feedparser
import aiohttp
import asyncio
from typing import List, Dict, Optional
from loguru import logger
import random

class NewsFetcher:
    def __init__(self):
        # Feed RSS per criptovalute
        self.rss_feeds = {
            "general": [
                "https://cointelegraph.com/rss",
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://cryptoslate.com/feed/",
                "https://bitcoinmagazine.com/feed",
                "https://decrypt.co/feed"
            ],
            "btc": [
                "https://cointelegraph.com/rss/tag/bitcoin",
                "https://www.coindesk.com/arc/outboundfeeds/rss/?q=bitcoin",
                "https://cryptopotato.com/feed/?tag=bitcoin"
            ],
            "eth": [
                "https://cointelegraph.com/rss/tag/ethereum",
                "https://www.coindesk.com/arc/outboundfeeds/rss/?q=ethereum",
                "https://cryptopotato.com/feed/?tag=ethereum"
            ],
            "sol": [
                "https://cointelegraph.com/rss/tag/solana",
                "https://www.coindesk.com/arc/outboundfeeds/rss/?q=solana",
                "https://cryptopotato.com/feed/?tag=solana"
            ]
        }
        self._cache = []
        self._last_fetch = None

    async def fetch_news(self, symbol: Optional[str] = None, limit: int = 5) -> List[str]:
        """Recupera notizie da RSS feeds, opzionalmente per un simbolo specifico."""
        if symbol:
            feeds = self.rss_feeds.get(symbol.lower(), self.rss_feeds["general"])
        else:
            feeds = self.rss_feeds["general"]
        
        # Mescola per avere varietà
        random.shuffle(feeds)
        news_titles = []
        
        for feed_url in feeds[:3]:  # limitiamo a 3 feed per evitare troppe richieste
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(feed_url, timeout=10) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            feed = feedparser.parse(text)
                            for entry in feed.entries[:3]:  # max 3 per feed
                                title = entry.get('title', '')
                                if title:
                                    news_titles.append(title)
            except Exception as e:
                logger.debug(f"⚠️ Errore fetch da {feed_url}: {e}")
        
        # Fallback: notizie simulate se non ne abbiamo ottenute
        if not news_titles:
            fallback_news = {
                "btc": [
                    "Bitcoin supera i 70.000$ dopo dati inflazione USA",
                    "ETF su Bitcoin registrano flussi record",
                    "Bitcoin adottato come valuta legale in un nuovo paese"
                ],
                "eth": [
                    "Ethereum upgrade migliora scalabilità e riduce fee",
                    "ETF su Ethereum attirano investitori istituzionali",
                    "Sviluppi positivi per lo staking di Ethereum"
                ],
                "sol": [
                    "Solana registra un aumento delle transazioni",
                    "Nuove partnership per l'ecosistema Solana",
                    "Solana supera Ethereum in termini di velocità di transazione"
                ]
            }
            if symbol and symbol.lower() in fallback_news:
                news_titles = fallback_news[symbol.lower()]
            else:
                news_titles = [
                    "Mercati crypto in rialzo dopo dichiarazioni della Fed",
                    "Nuova regolamentazione crypto in Europa",
                    "Adozione crypto in aumento tra i pagamenti digitali"
                ]
        
        # Limita il numero di titoli
        return news_titles[:limit]

    async def fetch_news_for_all(self, limit_per_symbol: int = 3) -> Dict[str, List[str]]:
        """Recupera notizie per BTC, ETH, SOL e generali."""
        result = {
            "general": await self.fetch_news(limit=limit_per_symbol),
            "BTC": await self.fetch_news("btc", limit=limit_per_symbol),
            "ETH": await self.fetch_news("eth", limit=limit_per_symbol),
            "SOL": await self.fetch_news("sol", limit=limit_per_symbol)
        }
        return result
