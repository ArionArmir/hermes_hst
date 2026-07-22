"""Scarica e SIGILLA i simboli fuori universo che fanno da holdout pulito.

PERCHÉ FUORI UNIVERSO E NON "GLI ULTIMI 12 MESI"
Il walk-forward del 2026-07-16 ha testato l'intera finestra 2023-01 -> 2026-07
(fold 1-4), quindi nessuna porzione temporale è vergine per la famiglia di
ipotesi perlustrata (soglia/SL/TP/filtri, 41 tentativi registrati). Il periodo
2020-2023 è servito ad addestrare, ed è quindi in-sample per il modello.
Validare in avanti richiederebbe 5 anni di paper trading con l'edge osservato,
85 con quello deflazionato: non è praticabile. I simboli mai guardati sono
l'unica fonte pulita che resta.

COS'È "FUORI UNIVERSO"
NON i 7 simboli operativi: quelli GUARDATI, che sono molti di più. Lo screening
simboli del 2026-07-16 (17:22-17:24) ha scaricato e valutato ADA, LINK, LTC,
ATOM, DOT, AVAX, FIL, UNI, APT, ARB, INJ, NEAR, OP, POL, SUI — tutti bruciati.
Il primo tentativo di sigillo ne includeva 8 per questa distrazione.

Il criterio è quindi verificabile e non opinabile: `data/historical/` è il
registro di tutto ciò che è stato scaricato, e un simbolo mai scaricato non
può essere stato analizzato. Lo script RIFIUTA di sigillare qualunque simbolo
già presente lì.

Lo script scarica e basta: nessuna feature, nessun modello, nessuna statistica.
Guardare i dati è ciò che li consuma.

Uso:  venv/bin/python scripts/seal_holdout.py
"""
import hashlib
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

from src.data_collector import DataCollector, HISTORY_DAYS
from src.shared.holdout import HOLDOUT_DIR, MANIFEST_PATH

HISTORICAL_DIR = Path(__file__).parent.parent / "data" / "historical"

# Perpetual USDT liquidi, quotati da anni, MAI scaricati né valutati in alcun
# esperimento. Scelti per anzianità di quotazione e liquidità: nessun criterio
# che dipenda dai loro rendimenti (sceglierli guardando come si comportano
# sarebbe già una selezione, e li brucerebbe prima ancora di sigillarli).
OUT_OF_UNIVERSE = [
    "BCHUSDT", "ETCUSDT", "EOSUSDT", "XLMUSDT", "VETUSDT", "THETAUSDT",
    "ALGOUSDT", "ZECUSDT", "NEOUSDT", "IOTAUSDT", "AAVEUSDT", "SUSHIUSDT",
]


def _touched_symbols() -> set[str]:
    """Tutto ciò che è stato scaricato almeno una volta = potenzialmente visto."""
    if not HISTORICAL_DIR.exists():
        return set()
    return {p.name.split("_")[0] for p in HISTORICAL_DIR.glob("*.parquet")}


def main():
    if MANIFEST_PATH.exists():
        logger.error(f"❌ Sigillo già presente in {MANIFEST_PATH}. "
                     "Riscaricare cancellerebbe la garanzia di non-contaminazione. "
                     "Per aggiungere simboli, creare un NUOVO lotto sigillato.")
        return 1

    # Il controllo che il primo tentativo di sigillo non aveva, e che gli
    # sarebbe costato 8 simboli su 10
    touched = _touched_symbols()
    contaminati = sorted(set(OUT_OF_UNIVERSE) & touched)
    if contaminati:
        logger.error(f"❌ Questi simboli sono già in data/historical: {contaminati}. "
                     "Sono già stati scaricati, quindi potenzialmente valutati: "
                     "sigillarli darebbe un holdout finto. Sceglierne altri.")
        return 1

    HOLDOUT_DIR.mkdir(parents=True, exist_ok=True)
    collector = DataCollector()
    entries = {}

    for sym in OUT_OF_UNIVERSE:
        df = collector.download_historical(sym, timeframe="1h", days=HISTORY_DAYS)
        if df.empty:
            logger.warning(f"⚠️  {sym}: nessun dato, escluso dal lotto")
            continue
        path = HOLDOUT_DIR / f"{sym}_1h.parquet"
        df.to_parquet(path)
        entries[sym] = {
            "barre": int(len(df)),
            "da": str(df.index.min().date()),
            "a": str(df.index.max().date()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        # Solo metadati: quante barre e da quando. Nessun rendimento, nessuna
        # statistica — leggerli qui romperebbe il sigillo nell'atto di crearlo.
        logger.info(f"🔒 {sym}: {len(df):,} barre {df.index.min().date()} -> "
                    f"{df.index.max().date()}")

    manifest = {
        "sigillato_il": str(date.today()),
        "motivo": (
            "Nessun dato storico dei simboli già guardati è vergine: il walk-forward "
            "del 2026-07-16 ha testato 2023-01 -> 2026-07 con 41 configurazioni "
            "registrate, e 2020-2023 è servito ad addestrare. Il Deflated Sharpe del "
            "candidato migliore (+244.65, soglia 0.50) con il conteggio onesto dei "
            "tentativi è 21.4%: sotto il livello che la fortuna produce da sola. "
            "Questi simboli non sono mai stati scaricati, quindi mai analizzati."
        ),
        "regola": (
            "APRIRE UNA VOLTA SOLA, su un candidato unico già congelato, tramite "
            "src.shared.holdout.open_seal(), dichiarando i tentativi consumati. "
            "Ogni ricerca condotta qui trasforma l'holdout in un set di ricerca e "
            "ne annulla il valore: riscegliendo il meglio di 6 su un holdout, un "
            "edge vero di 0 si rilegge +156 (simulazione 100k, 2026-07-16)."
        ),
        "criterio_pulizia": (
            "Mai presenti in data/historical/, che è il registro di tutto ciò che "
            "è stato scaricato. Un simbolo mai scaricato non può essere stato "
            "analizzato. Verificato automaticamente da questo script."
        ),
        "simboli_gia_bruciati": sorted(touched),
        "stato": "SIGILLATO",
        "aperto_da": None,
        "simboli": entries,
    }
    with open(MANIFEST_PATH, "w") as f:
        yaml.safe_dump(manifest, f, sort_keys=False, allow_unicode=True)

    logger.info(f"\n✅ Sigillo creato: {len(entries)} simboli in {HOLDOUT_DIR}")
    logger.info(f"   Esclusi perché già guardati: {len(touched)} simboli")
    logger.info(f"   Manifesto: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
