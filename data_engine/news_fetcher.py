"""
Fetch di notizie crypto da RSS, per asset dinamici (derivati dai simboli in
config, non hardcoded). Regola di integrità: se i feed non rispondono si
restituisce una lista VUOTA — mai notizie inventate. Il vecchio fallback con
titoli simulati (tutti tendenzialmente rialzisti) iniettava un bias positivo
sistematico nel sentiment proprio quando mancavano i dati reali.
"""
import asyncio
import random
from typing import Dict, List, Optional

import aiohttp
import feedparser
from loguru import logger

# Termine di ricerca per i feed taggati; asset non mappato → ticker minuscolo
ASSET_FEED_QUERY = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "XRP": "xrp",
    "BNB": "bnb",
    "TRX": "tron",
    "ADA": "cardano",
}

GENERAL_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptoslate.com/feed/",
    "https://bitcoinmagazine.com/feed",
    "https://decrypt.co/feed",
]


class NewsFetcher:
    def __init__(self):
        self.general_feeds = list(GENERAL_FEEDS)

    def _feeds_for_asset(self, asset: str) -> List[str]:
        query = ASSET_FEED_QUERY.get(asset.upper(), asset.lower())
        return [
            f"https://cointelegraph.com/rss/tag/{query}",
            f"https://www.coindesk.com/arc/outboundfeeds/rss/?q={query}",
            f"https://cryptopotato.com/feed/?tag={query}",
        ]

    async def fetch_news(self, asset: Optional[str] = None, limit: int = 5) -> List[str]:
        """Titoli recenti per un asset (o generali se asset=None). Lista
        vuota se nessun feed risponde: la mancanza di notizie è
        un'informazione, non va riempita con contenuti simulati."""
        feeds = self._feeds_for_asset(asset) if asset else list(self.general_feeds)
        random.shuffle(feeds)
        news_titles: List[str] = []

        for feed_url in feeds[:3]:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            feed = feedparser.parse(text)
                            for entry in feed.entries[:3]:
                                title = entry.get('title', '')
                                if title:
                                    news_titles.append(title)
            except Exception as e:
                logger.debug(f"⚠️ Errore fetch da {feed_url}: {e}")

        return news_titles[:limit]

    async def fetch_news_for_all(self, assets: List[str], limit_per_symbol: int = 3) -> Dict[str, List[str]]:
        """Notizie generali + per ciascun asset richiesto, in parallelo
        (sequenziale, con 8 asset × 3 feed × timeout 10s, poteva impiegare
        minuti)."""
        results = await asyncio.gather(
            self.fetch_news(limit=limit_per_symbol),
            *[self.fetch_news(asset, limit=limit_per_symbol) for asset in assets],
        )
        news: Dict[str, List[str]] = {"general": results[0]}
        for asset, titles in zip(assets, results[1:]):
            news[asset] = titles
        return news
