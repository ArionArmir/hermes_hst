"""Riscarica i parquet mensili dei fondi del Piano (VWCE, S&P, EURUSD).

Agganciato al timer mensile (hermes-report): la dashboard legge sempre il
parquet locale — mai lenta, mai congelata. Cadenza mensile = giusta per dati
mensili. Idempotente, tollerante al fallimento per-simbolo.

Uso:  venv/bin/python scripts/refresh_invest_series.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.invest.drawdown import refresh_yahoo_cache

if __name__ == "__main__":
    for nome, esito in refresh_yahoo_cache().items():
        logger.info(f"{nome}: {esito}")
