"""Semaforo del carry — monitor DESCRITTIVO del regime (fase 1 dello studio
di fattibilità medio termine).

La strategia di medio termine promossa (carry_v1) dorme per regime compresso,
non per bocciatura. Questo modulo misura il regime corrente e lo colloca
nella distribuzione storica: una sveglia, non un segnale. L'eventuale
attivazione del carry richiederebbe un pre-registro proprio (vedi esito
carry_v1: un filtro di regime scelto guardando lo spaccato è la mossa vietata).

Parti pure testabili separate dalle chiamate di rete (che degradano con
grazia: il rapporto non deve mai crashare per un'API giù).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

_ROOT = Path(__file__).resolve().parents[2]
FUNDING_DIR = _ROOT / "data" / "funding"
FAPI = "https://fapi.binance.com"

# Spaccato annuale della primaria carry_v1 (misurato, per riferimento a video)
STORICO_CARRY = {"2021": 0.3549, "2022": 0.0038, "2023": 0.0649,
                 "2024": 0.1305, "2025": 0.0189, "2026H1": -0.0053}


def annualizza_funding(rates: list[float], giorni_finestra: float) -> float:
    """Somma del funding nella finestra -> tasso annuo, INDIPENDENTE dalla
    frequenza di settlement (8h/4h/1h). Prima assumeva 3 eventi/giorno
    (media × 3 × 365) e sballava di ~8x sui simboli a funding orario, oltre a
    campionarne solo ~4 giorni per via del limit tarato sull'8h."""
    if not rates or giorni_finestra <= 0:
        return 0.0
    return sum(rates) * 365 / giorni_finestra


def fascia_regime(mediana_annua: float) -> tuple[str, str]:
    """Fasce DESCRITTIVE, derivate dalla storia misurata (mediana 5.5y: +8%;
    2021/2024 ricchi; 2025-26 compressi). Descrivono, non raccomandano."""
    if mediana_annua < 0.02:
        return "COMPRESSO", "il regime 2025-26: il flusso non paga la soglia operativa"
    if mediana_annua < 0.08:
        return "NELLA NORMA", "flusso presente ma sotto la mediana storica di picco"
    return "RICCO", "livelli 2021/2024: il regime in cui il carry pagava davvero"


def annualizza_basis(prezzo_fut: float, prezzo_spot: float,
                     giorni_a_scadenza: float) -> float:
    if giorni_a_scadenza <= 0 or prezzo_spot <= 0:
        return 0.0
    return (prezzo_fut - prezzo_spot) / prezzo_spot * 365 / giorni_a_scadenza


def scadenza_da_simbolo(symbol: str) -> datetime:
    """BTCUSDT_260925 -> 2026-09-25 08:00 UTC (regolamento delivery)."""
    suffisso = symbol.rsplit("_", 1)[1]
    return datetime.strptime(f"20{suffisso} 08:00 +0000", "%Y%m%d %H:%M %z")


# ------------------------------------------------------------------ rete --

def funding_corrente(giorni: int = 30) -> dict | None:
    """Funding trailing per i simboli dell'universo carry, dall'API live."""
    simboli = sorted(p.name.split("_funding")[0] for p in FUNDING_DIR.glob("*.parquet"))
    if not simboli:
        return None
    da = datetime.now(timezone.utc).timestamp() * 1000 - giorni * 86400_000
    per_simbolo = {}
    for s in simboli:
        try:
            # limit=1000 (max Binance): copre 30gg anche a funding orario
            # (~720 eventi), dove 3*giorni+10 ne prendeva solo ~4 giorni.
            r = requests.get(f"{FAPI}/fapi/v1/fundingRate",
                             params={"symbol": s, "limit": 1000}, timeout=10)
            eventi = [float(e["fundingRate"]) for e in r.json()
                      if float(e.get("fundingTime", 0)) >= da]
            if eventi:
                per_simbolo[s] = annualizza_funding(eventi, giorni)
        except Exception:
            continue
    if len(per_simbolo) < 10:
        return None
    serie = pd.Series(per_simbolo)
    return {"mediana": float(serie.median()),
            "positivi": int((serie > 0).sum()), "totale": len(serie)}


def percentile_storico(mediana_corrente: float) -> float | None:
    """Dove sta il funding di oggi nella distribuzione mensile 2020-2026."""
    mensili = []
    for p in FUNDING_DIR.glob("*.parquet"):
        d = pd.read_parquet(p)
        m = (d.set_index("calc_time")["last_funding_rate"]
               .groupby(lambda t: t.to_period("M")).mean() * 3 * 365)
        mensili.append(m)
    if not mensili:
        return None
    per_mese = pd.concat(mensili, axis=1).median(axis=1).dropna()
    return float((per_mese < mediana_corrente).mean())


def basis_corrente() -> dict | None:
    """Basis annualizzato del trimestrale piu' vicino, BTC ed ETH."""
    out = {}
    adesso = datetime.now(timezone.utc)
    for sott in ("BTC", "ETH"):
        try:
            info = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=15).json() \
                if not out.get("_info") else out["_info"]
            out["_info"] = info
            trimestrali = sorted(
                s["symbol"] for s in info["symbols"]
                if s["symbol"].startswith(f"{sott}USDT_")
                and s.get("contractType") == "CURRENT_QUARTER")
            if not trimestrali:
                continue
            sym = trimestrali[0]
            fut = float(requests.get(f"{FAPI}/fapi/v1/ticker/price",
                                     params={"symbol": sym}, timeout=10).json()["price"])
            spot = float(requests.get("https://api.binance.com/api/v3/ticker/price",
                                      params={"symbol": f"{sott}USDT"},
                                      timeout=10).json()["price"])
            giorni = (scadenza_da_simbolo(sym) - adesso).total_seconds() / 86400
            out[sott] = {"simbolo": sym, "giorni": round(giorni, 1),
                         "basis_annuo": annualizza_basis(fut, spot, giorni)}
        except Exception:
            continue
    out.pop("_info", None)
    return out or None
