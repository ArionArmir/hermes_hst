"""Scarica l'universo esteso di docs/PRE_REGISTRO_BREADTH.md e ne valida
l'allineamento storico.

L'universo e' una REGOLA letta dalle fonti, non una lista: scrivendola a mano
si dimenticano simboli (e' successo con UNIUSDT, che risultava "disponibile"
pur essendo gia' in data/historical).

Uso:  venv/bin/python scripts/breadth_download.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import yaml
from loguru import logger

from src.data_collector import DataCollector, HISTORY_DAYS
from src.shared.holdout import sealed_symbols

HISTORICAL = Path(__file__).parent.parent / "data" / "historical"
ONBOARD_LIMITE = 1609459200000        # 2021-01-01, dal pre-registro
ANNI_MINIMI = 5.0                     # sotto questa soglia il run si ferma


def universo() -> list[str]:
    """Tutti i perpetual USDT quotati prima del 2021-01-01 e ancora attivi,
    esclusi i sigillati. Nessun filtro di volume: con posizioni da 150 USDT la
    liquidita' non vincola, e sarebbe una manopola in piu' da tarare."""
    info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    attivi = [
        s["symbol"] for s in info["symbols"]
        if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
        and s.get("onboardDate", 0) and s["onboardDate"] < ONBOARD_LIMITE
    ]
    return sorted(set(attivi) - set(sealed_symbols()))


def main():
    univ = universo()
    sigillati = set(sealed_symbols())
    assert not (set(univ) & sigillati), "un sigillato e' finito nell'universo di ricerca"

    collector = DataCollector()

    # Non basta che il parquet ESISTA: deve essere COMPLETO. La regola
    # dell'universo impone quotazione pre-2021, quindi ogni simbolo eleggibile
    # deve avere >= ANNI_MINIMI di storia; se ne ha meno il parquet e'
    # troncato, non il simbolo giovane.
    #
    # Serve davvero: 9 simboli (ADA, ATOM, AVAX, DOT, FIL, LINK, LTC, NEAR,
    # UNI) erano stati scaricati durante lo screening del 2026-07-16, quando
    # HISTORY_DAYS era ancora 365. Avevano 8.760 barre esatte (un anno tondo) e
    # troncavano l'intersezione dei 47 simboli da 5.5 anni a 0.99 - cioe' ci
    # avrebbero riportati al regime da 15-40 trade per fold. Un controllo di
    # sola presenza li aveva saltati.
    da_scaricare = []
    for s in univ:
        path = HISTORICAL / f"{s}_1h.parquet"
        if not path.exists():
            da_scaricare.append((s, "assente"))
            continue
        try:
            d = collector.load_historical(s, timeframe="1h")
            anni = (d.index.max() - d.index.min()).days / 365.25
            if anni < ANNI_MINIMI:
                da_scaricare.append((s, f"troncato a {anni:.2f} anni"))
        except Exception:
            da_scaricare.append((s, "illeggibile"))

    logger.info(f"Universo da regola: {len(univ)} simboli | completi: "
                f"{len(univ) - len(da_scaricare)} | da (ri)scaricare: {len(da_scaricare)}")
    for s, perche in da_scaricare:
        logger.info(f"    {s:12s} {perche}")

    for i, (sym, _) in enumerate(da_scaricare, 1):
        df = collector.download_historical(sym, timeframe="1h", days=HISTORY_DAYS)
        if df.empty:
            logger.warning(f"[{i}/{len(da_scaricare)}] {sym}: vuoto, escluso")
            continue
        collector.save_to_parquet(df, sym)
        logger.info(f"[{i}/{len(da_scaricare)}] {sym}: {len(df):,} barre "
                    f"{df.index.min().date()} -> {df.index.max().date()}")

    # ---- Validazione obbligatoria del pre-registro ----
    # _align_common_index fa l'INTERSEZIONE: buchi interni o storie disallineate
    # accorcerebbero la finestra in silenzio. Meglio fermarsi che misurare su
    # una finestra diversa da quella dichiarata.
    logger.info("\nValidazione allineamento (intersezione degli indici)...")
    comune = None
    per_simbolo = {}
    for sym in univ:
        try:
            d = collector.load_historical(sym, timeframe="1h")
        except Exception as e:
            logger.warning(f"  {sym}: illeggibile ({e}), escluso")
            continue
        if d is None or d.empty:
            continue
        per_simbolo[sym] = d
        comune = d.index if comune is None else comune.intersection(d.index)

    anni = (comune.max() - comune.min()).days / 365.25
    logger.info(f"  simboli leggibili: {len(per_simbolo)}")
    logger.info(f"  intersezione: {len(comune):,} barre "
                f"{comune.min().date()} -> {comune.max().date()} = {anni:.2f} anni")

    # Chi accorcia di piu' la finestra?
    tardivi = sorted(((d.index.min(), s) for s, d in per_simbolo.items()), reverse=True)[:5]
    logger.info("  storie che iniziano piu' tardi:")
    for ts, s in tardivi:
        logger.info(f"    {s:12s} da {ts.date()}")

    if anni < ANNI_MINIMI:
        logger.error(f"\n❌ STOP: intersezione {anni:.2f} anni, sotto il minimo "
                     f"di {ANNI_MINIMI}. Il pre-registro prevede di fermarsi invece "
                     f"di procedere su una finestra silenziosamente accorciata.")
        return 1

    logger.info(f"\n✅ Allineamento valido: {anni:.2f} anni su {len(per_simbolo)} simboli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
