"""Paper executor del carry — servizio (docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md).

Ciclo orario: accredita il funding reale (dall'API) alle posizioni di carta;
il lunedì 00 UTC ribilancia con la regola della primaria promossa (W30,
all-positive). Nessuna chiave di trading: il denaro non esiste per costruzione.

Heartbeat su Redis (`heartbeat_carry`); stato in data/carry_paper/.
"""
import time
from datetime import datetime, timedelta, timezone

import redis as redis_sync
import requests
from loguru import logger

from src.carry.paper import (accredita_funding, apri_posizione, carica_stato,
                             chiudi_posizione, salva_stato, selezione_w30,
                             serve_ribilanciamento)
from src.research.carry_monitor import FUNDING_DIR

FAPI = "https://fapi.binance.com"
SPOT_API = "https://api.binance.com"


def _universo() -> list[str]:
    return sorted(p.name.split("_funding")[0] for p in FUNDING_DIR.glob("*.parquet"))


def _eventi_funding(simbolo: str, da: datetime) -> list[float]:
    r = requests.get(f"{FAPI}/fapi/v1/fundingRate",
                     params={"symbol": simbolo, "limit": 120}, timeout=10)
    return [float(e["fundingRate"]) for e in r.json()
            if datetime.fromtimestamp(e["fundingTime"] / 1000, tz=timezone.utc) > da]


def _media_trailing_30g(simbolo: str) -> float | None:
    da = datetime.now(timezone.utc) - timedelta(days=30)
    eventi = _eventi_funding(simbolo, da)
    return sum(eventi) / len(eventi) if eventi else None


def _basis(simbolo: str) -> float | None:
    try:
        perp = float(requests.get(f"{FAPI}/fapi/v1/ticker/price",
                                  params={"symbol": simbolo}, timeout=10).json()["price"])
        spot = float(requests.get(f"{SPOT_API}/api/v3/ticker/price",
                                  params={"symbol": simbolo}, timeout=10).json()["price"])
        return (perp - spot) / spot
    except Exception:
        return None


def ciclo(stato: dict) -> list[dict]:
    adesso = datetime.now(timezone.utc)
    eventi = []

    # 1) accredito del funding reale alle posizioni aperte
    for sym in list(stato["posizioni"]):
        pos = stato["posizioni"][sym]
        da = datetime.fromisoformat(pos.get("ultimo_accredito", pos["aperta"]))
        try:
            rates = _eventi_funding(sym, da)
        except Exception:
            continue
        incasso = accredita_funding(stato, sym, rates, adesso)
        if incasso:
            eventi.append({"evento": "funding", "simbolo": sym,
                           "eventi": len(rates), "usdt": round(incasso, 6)})

    # 2) ribilanciamento settimanale (lunedì 00 UTC)
    if serve_ribilanciamento(stato.get("ultimo_ribilanciamento"), adesso):
        logger.info("ribilanciamento settimanale...")
        medie = {s: _media_trailing_30g(s) for s in _universo()}
        target = selezione_w30(medie)
        correnti = set(stato["posizioni"])
        for sym in sorted(correnti - target):
            b = _basis(sym)
            if b is not None:
                eventi.append(chiudi_posizione(stato, sym, b))
        for sym in sorted(target - correnti):
            b = _basis(sym)
            if b is not None:
                eventi.append(apri_posizione(stato, sym, b, adesso))
        stato["ultimo_ribilanciamento"] = adesso.isoformat()
        stato["ribilanciamenti"] = stato.get("ribilanciamenti", 0) + 1
        eventi.append({"evento": "ribilanciamento", "posizioni": len(stato["posizioni"]),
                       "selezionati": len(target)})
        logger.info(f"posizioni aperte: {len(stato['posizioni'])}")
    return eventi


def main():
    logger.info("Paper executor del carry: primaria carry_v1 (W30 all-positive), "
                "notional di carta, nessuna chiave di trading")
    r = redis_sync.Redis(decode_responses=True)
    while True:
        try:
            stato = carica_stato()
            eventi = ciclo(stato)
            salva_stato(stato, eventi)
            try:
                r.set("heartbeat_carry", datetime.now(timezone.utc).isoformat())
            except Exception:
                pass
        except Exception as e:
            logger.error(f"ciclo fallito: {e}")
        time.sleep(3600)


if __name__ == "__main__":
    main()
