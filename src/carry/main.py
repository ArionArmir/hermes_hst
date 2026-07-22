"""Paper executor del carry — servizio (docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md).

Ciclo orario: accredita il funding reale (dall'API) alle posizioni di carta;
il lunedì 00 UTC ribilancia con la regola della primaria promossa (W30,
all-positive). Nessuna chiave di trading: il denaro non esiste per costruzione.

Heartbeat su Redis (`heartbeat_carry`); stato in data/carry_paper/.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import redis as redis_sync
import requests
from loguru import logger

from src.carry.paper import (accredita_funding, apri_posizione, carica_stato,
                             chiudi_posizione, salva_stato, selezione_w30,
                             serve_ribilanciamento)
from src.research.carry_monitor import (FUNDING_DIR, basis_corrente,
                                        fascia_regime, funding_corrente,
                                        percentile_storico)

FAPI = "https://fapi.binance.com"
SPOT_API = "https://api.binance.com"


def _universo() -> list[str]:
    return sorted(p.name.split("_funding")[0] for p in FUNDING_DIR.glob("*.parquet"))


def _eventi_funding(simbolo: str, da: datetime) -> list[tuple[datetime, float]]:
    """(fundingTime, rate) degli eventi dopo `da`. Il tempo serve a fissare il
    high-water mark al MAX fundingTime accreditato (revisione branch
    2026-07-21): fissarlo a un timestamp pre-fetch causava il doppio-accredito
    di un settlement avvenuto tra la cattura di `adesso` e la fetch."""
    # limit 1000 (max Binance) invece di 120 (revisione branch 2026-07-21):
    # 120 eventi = ~40 giorni a funding 8h ma solo ~5 a 1h → la media trailing
    # 30g dei simboli a funding 1h/4h vedeva pochi giorni, e un downtime lungo
    # sotto-contava gli accrediti persi.
    r = requests.get(f"{FAPI}/fapi/v1/fundingRate",
                     params={"symbol": simbolo, "limit": 1000}, timeout=10)
    out = []
    for e in r.json():
        t = datetime.fromtimestamp(e["fundingTime"] / 1000, tz=timezone.utc)
        if t > da:
            out.append((t, float(e["fundingRate"])))
    return out


def _media_trailing_30g(simbolo: str) -> float | None:
    da = datetime.now(timezone.utc) - timedelta(days=30)
    rates = [r for _, r in _eventi_funding(simbolo, da)]
    return sum(rates) / len(rates) if rates else None


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
            eventi_f = _eventi_funding(sym, da)
        except Exception:
            continue
        incasso = accredita_funding(stato, sym, eventi_f)
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


def pubblica_semaforo(r):
    """Calcola il semaforo (funding mediano, percentile, basis) e lo pubblica
    su Redis, così la dashboard lo LEGGE invece di fare decine di chiamate REST
    a Binance al primo render (era il tab Carry lento a freddo). Qui è un lavoro
    di background orario: nessuna UI da bloccare."""
    try:
        fc = funding_corrente()
        if not fc:
            return
        fascia, nota = fascia_regime(fc["mediana"])
        payload = {**fc, "percentile": percentile_storico(fc["mediana"]),
                   "fascia": fascia, "nota": nota, "basis": basis_corrente(),
                   "ts": datetime.now(timezone.utc).isoformat()}
        r.set("carry_semaforo", json.dumps(payload))
    except Exception as e:
        logger.warning(f"pubblicazione semaforo fallita (non bloccante): {e}")


def main():
    logger.info("Paper executor del carry: primaria carry_v1 (W30 all-positive), "
                "notional di carta, nessuna chiave di trading")
    r = redis_sync.Redis(decode_responses=True)
    while True:
        try:
            stato = carica_stato()
            eventi = ciclo(stato)
            salva_stato(stato, eventi)
            pubblica_semaforo(r)
            try:
                r.set("heartbeat_carry", datetime.now(timezone.utc).isoformat())
            except Exception:
                pass
        except Exception as e:
            logger.error(f"ciclo fallito: {e}")
        time.sleep(3600)


if __name__ == "__main__":
    main()
