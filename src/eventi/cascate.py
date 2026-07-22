"""Rilevatore di cascate di liquidazioni — fase 2 del feed eventi.

Definizione DICHIARATA (calibrata il 2026-07-21 sullo storico orario
Coinalyze, 3-8 mesi per simbolo): cascata di simbolo = quantità liquidata
nell'ultima ora sopra il percentile 99.5 della distribuzione storica delle
ore con liquidazioni di quel simbolo (~3 scatti/mese a simbolo); cascata di
MERCATO = almeno 3 dei 7 simboli in cascata insieme (~4/mese misurati).

Unità: quantità di asset base, le stesse del recorder e di Coinalyze
(verificato incrociando l'ora sovrapposta il 2026-07-20) — entrambi
derivano dallo stesso stream campionato di Binance, quindi confrontabili.
Solo descrizione a posteriori: il feed non è un segnale e non notifica.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
STORICO_ORARIO = _ROOT / "data" / "liquidations_aggregate" / "coinalyze_1h.parquet"
DIR_RECORDER = _ROOT / "data" / "liquidations"
PERCENTILE = 0.995
MIN_SIMBOLI_MERCATO = 3
FINESTRA = timedelta(hours=1)


def soglie_da_storico(orario: pd.DataFrame, percentile: float = PERCENTILE) -> dict:
    """{simbolo motore: soglia qty} dal parquet Coinalyze (simboli tipo
    'BTCUSDT_PERP.A' → 'BTCUSDT'). La distribuzione è quella delle ore CON
    liquidazioni: le ore mute non compaiono nel dato, e va bene così —
    anche la finestra corrente si confronta solo quando ha eventi."""
    if orario is None or not len(orario):
        return {}
    tot = orario.assign(tot=orario["liq_long"] + orario["liq_short"],
                        simbolo=orario["symbol"].str.split("_").str[0])
    return tot.groupby("simbolo")["tot"].quantile(percentile).to_dict()


def rileva(eventi_recorder: pd.DataFrame, soglie: dict,
           adesso: datetime) -> list[dict]:
    """Eventi cascata dall'ultima ora del recorder. La chiave di dedup
    include l'ora: una cascata che dura più giri del watchdog resta UN
    evento; una nuova cascata ore dopo è una nuova notizia."""
    from src.eventi.osservatore import _evento

    if eventi_recorder is None or not len(eventi_recorder) or not soglie:
        return []
    finestra = eventi_recorder[eventi_recorder["ts"] >= adesso - FINESTRA]
    qty = finestra.groupby("symbol")["qty"].sum()
    ora = f"{adesso:%Y-%m-%d %H}"
    eventi = []
    in_cascata = []
    for simbolo, soglia in sorted(soglie.items()):
        q = float(qty.get(simbolo, 0.0))
        if q >= soglia:
            in_cascata.append(simbolo)
            e = _evento("cascata", "nota",
                        f"Cascata di liquidazioni su {simbolo}",
                        f"{q:,.0f} {simbolo.replace('USDT', '')} nell'ultima ora "
                        f"(soglia P{PERCENTILE:.1%}: {soglia:,.0f})")
            e["chiave"] = f"cascata:{simbolo}:{ora}"
            eventi.append(e)
    if len(in_cascata) >= MIN_SIMBOLI_MERCATO:
        e = _evento("cascata", "nota",
                    f"Cascata di MERCATO: {len(in_cascata)} simboli insieme",
                    ", ".join(in_cascata))
        e["chiave"] = f"cascata:mercato:{ora}"
        eventi.append(e)
    return eventi


def eventi_cascata(adesso: datetime | None = None) -> list[dict]:
    """Il giro completo per l'osservatore: soglie dallo storico, finestra
    dal parquet del recorder (più il giorno precedente vicino a mezzanotte)."""
    adesso = adesso or datetime.now(timezone.utc)
    if not STORICO_ORARIO.exists():
        return []
    soglie = soglie_da_storico(pd.read_parquet(STORICO_ORARIO))
    pezzi = []
    for giorno in {adesso.date(), (adesso - FINESTRA).date()}:
        f = DIR_RECORDER / f"{giorno}.parquet"
        if f.exists():
            pezzi.append(pd.read_parquet(f))
    if not pezzi:
        return []
    return rileva(pd.concat(pezzi, ignore_index=True), soglie, adesso)
