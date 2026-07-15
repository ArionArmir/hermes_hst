"""
Persistenza SQLite (data/hermes.db) per trade, decisioni sui segnali e
sentiment. Sostituisce il pattern CSV "leggi tutto + concat + riscrivi"
(O(n²) e corruttibile) come fonte di verità interrogabile; i CSV storici
restano come export append-only per compatibilità (verify_overnight).

Scelte:
- WAL + busy_timeout: la dashboard legge mentre l'engine scrive, senza lock;
- connessioni brevi per scrittura (pochi eventi al minuto: la semplicità
  vale più del pooling);
- schema creato al volo a ogni connessione (IF NOT EXISTS, costo ~zero):
  nessuno script di migrazione da ricordare;
- chi scrive dal percorso di trading DEVE avvolgere in try/except: un errore
  di persistenza non deve mai fermare il trading (vedi engine._record_signal).

La tabella `signals` registra OGNI decisione dell'engine sui segnali ML,
inclusi quelli scartati e il perché (outcome): è la risposta a "perché il
bot non sta tradando?" senza grep nei log.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

DB_PATH = Path("data/hermes.db")

# Esiti possibili di signals.outcome (per riferimento e per la dashboard)
SIGNAL_OUTCOMES = (
    "OPENED",             # posizione aperta
    "REVERSED",           # posizione invertita (chiusa + riaperta opposta)
    "CLOSE",              # segnale close eseguito
    "LOW_CONFIDENCE",     # confidenza pesata sotto soglia
    "SENTIMENT_VETO",     # sentiment fortemente contrario
    "ENTRY_COOLDOWN",     # chiusura recente sul simbolo
    "REVERSE_COOLDOWN",   # posizione troppo giovane per invertirla
    "REVERSE_HYSTERESIS", # confidenza insufficiente per invertire
    "ALREADY_OPEN",       # posizione già aperta nella stessa direzione
    "PATTERN_REJECT",     # respinto dal VolumePatternAnalyzer
    "NO_PRICE",           # prezzo non disponibile
    "NO_CAPITAL",         # sizing nullo
    "EXPOSURE_CAP",       # cap di margine portafoglio raggiunto
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    exit REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_gross REAL,
    fees REAL,
    reason TEXT,
    capital_after REAL
);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    weighted_confidence REAL,
    outcome TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);

CREATE TABLE IF NOT EXISTS sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    asset TEXT NOT NULL,
    score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sentiment_timestamp ON sentiment(timestamp);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def insert_trade(symbol: str, side: str, entry: float, exit_price: float, pnl: float,
                 reason: str, pnl_gross: Optional[float] = None, fees: float = 0.0,
                 capital_after: Optional[float] = None,
                 timestamp: Optional[str] = None, db_path: Optional[Path] = None):
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, entry, exit, pnl, pnl_gross, fees, reason, capital_after) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp or _now_iso(), symbol, side, entry, exit_price, pnl,
             pnl_gross if pnl_gross is not None else pnl, fees, reason, capital_after),
        )


def insert_signal(symbol: str, action: str, outcome: str, confidence: Optional[float] = None,
                  weighted_confidence: Optional[float] = None, detail: str = "",
                  timestamp: Optional[str] = None, db_path: Optional[Path] = None):
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO signals (timestamp, symbol, action, confidence, weighted_confidence, outcome, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (timestamp or _now_iso(), symbol, action, confidence, weighted_confidence, outcome, detail),
        )


def insert_sentiment(scores: dict, timestamp: Optional[str] = None, db_path: Optional[Path] = None):
    """Una riga per asset (aggregate incluso), stesso timestamp di ciclo."""
    ts = timestamp or _now_iso()
    rows = [(ts, asset, float(score)) for asset, score in scores.items()
            if isinstance(score, (int, float))]
    if not rows:
        return
    with _connect(db_path) as conn:
        conn.executemany("INSERT INTO sentiment (timestamp, asset, score) VALUES (?, ?, ?)", rows)


def _read_sql(query: str, limit: int, db_path: Optional[Path]) -> pd.DataFrame:
    with _connect(db_path) as conn:
        # Difensivo: con pandas 3 le stringhe sono arrow-backed di default e
        # la loro costruzione può segfaultare in processi che mescolano
        # librerie native (riprodotto in ambiente di test multi-pagina, vedi
        # tests/test_dashboard_pages.py). Lo storage "python" per queste
        # piccole tabelle è funzionalmente identico e tiene questo modulo
        # fuori da quel percorso.
        with pd.option_context("mode.string_storage", "python"):
            return pd.read_sql_query(query, conn, params=(limit,))


def read_trades(limit: int = 1000, db_path: Optional[Path] = None) -> pd.DataFrame:
    return _read_sql("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", limit, db_path)


def read_signals(limit: int = 200, db_path: Optional[Path] = None) -> pd.DataFrame:
    return _read_sql("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", limit, db_path)


def read_sentiment(limit: int = 1000, db_path: Optional[Path] = None) -> pd.DataFrame:
    return _read_sql("SELECT * FROM sentiment ORDER BY timestamp DESC LIMIT ?", limit, db_path)
