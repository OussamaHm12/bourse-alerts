from moroccan_stock_intelligence.scrapers.base import MarketDataScraper, ScraperError
from moroccan_stock_intelligence.scrapers.bmce import BMCECapitalScraper
from moroccan_stock_intelligence.scrapers.casablanca import CasablancaBourseScraper
from moroccan_stock_intelligence.scrapers.cdg import CDGCapitalScraper

__all__ = [
    "BMCECapitalScraper",
    "CDGCapitalScraper",
    "CasablancaBourseScraper",
    "MarketDataScraper",
    "ScraperError",
]
