"""Logica pura del registratore di liquidazioni (testabile senza rete).

Le liquidazioni storiche non si comprano sotto i $299/mese e Binance non le
pubblica nei dump: registrarle da oggi crea il dataset che non si può
ricomprare. Ogni giorno senza registratore è un giorno perso per sempre —
questo modulo esiste per smettere di perderne.

Solo raccolta: nessuna decisione, nessun segnale. Il dato si giudicherà con
un pre-registro quando ce ne sarà abbastanza (la data di nascita del dataset
è nel registro esperimenti, famiglia dati_liquidazioni).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "data" / "liquidations"

CAMPI = ["ts", "symbol", "side", "qty", "prezzo_medio", "notional_usdt"]


def normalizza(evento: dict) -> dict | None:
    """Da messaggio forceOrder del WebSocket a riga del dataset.

    `side` è il lato dell'ORDINE di liquidazione: SELL = un long è stato
    liquidato (la posizione viene venduta), BUY = uno short. Righe malformate
    tornano None: meglio perdere un evento che scrivere spazzatura.
    """
    try:
        o = evento["o"]
        qty = float(o["z"])                      # quantità eseguita
        prezzo = float(o["ap"])                  # prezzo medio di esecuzione
        return {
            "ts": datetime.fromtimestamp(int(o["T"]) / 1000, tz=timezone.utc),
            "symbol": str(o["s"]),
            "side": str(o["S"]),
            "qty": qty,
            "prezzo_medio": prezzo,
            "notional_usdt": qty * prezzo,
        }
    except (KeyError, TypeError, ValueError):
        return None


class BufferGiornaliero:
    """Accumula righe e le scrive nel parquet del giorno (merge col file
    esistente: i restart non perdono né duplicano giornate)."""

    def __init__(self, out_dir: Path = OUT_DIR, max_righe: int = 200):
        self.out_dir = out_dir
        self.max_righe = max_righe
        self.righe: list[dict] = []

    def aggiungi(self, riga: dict) -> bool:
        """True se dopo l'aggiunta serve un flush."""
        self.righe.append(riga)
        return len(self.righe) >= self.max_righe

    def flush(self) -> int:
        if not self.righe:
            return 0
        self.out_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(self.righe)[CAMPI]
        scritte = 0
        for giorno, gruppo in df.groupby(df["ts"].dt.date):
            path = self.out_dir / f"{giorno}.parquet"
            if path.exists():
                gruppo = (pd.concat([pd.read_parquet(path), gruppo])
                            .drop_duplicates(subset=["ts", "symbol", "qty"])
                            .sort_values("ts"))
            gruppo.reset_index(drop=True).to_parquet(path)
            scritte += len(gruppo)
        n = len(self.righe)
        self.righe = []
        return n
