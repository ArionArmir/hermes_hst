"""Logica pura del paper executor del carry (testabile senza rete).

Esegue la primaria promossa di carry_v1 — W30, all-positive, ribilanciamento
il lunedì 00 UTC — su posizioni simulate, con la STESSA contabilità del
backtest (src/research/carry.py): lo scopo è misurare la divergenza tra
backtest e realtà, non inventare una contabilità nuova.

Il denaro non esiste: notional di carta, nessuna chiave di trading.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.research.carry import COSTO_APERTURA, COSTO_CHIUSURA

_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = _ROOT / "data" / "carry_paper"
STATE = STATE_DIR / "state.json"
LEDGER = STATE_DIR / "ledger.jsonl"

NOTIONAL = 100.0                      # USDT di carta per posizione


def selezione_w30(medie_funding: dict[str, float | None]) -> set[str]:
    """All-positive: la regola della primaria promossa. Nessun parametro."""
    return {s for s, m in medie_funding.items() if m is not None and m > 0}


def ultimo_lunedi_00(adesso: datetime) -> datetime:
    """Il lunedì 00:00 UTC più recente (incluso oggi se è lunedì)."""
    giorni = adesso.weekday()          # lunedì = 0
    base = (adesso - timedelta(days=giorni)).replace(hour=0, minute=0,
                                                     second=0, microsecond=0)
    return base


def serve_ribilanciamento(ultimo: str | None, adesso: datetime) -> bool:
    """Mai ribilanciato, o l'ultimo è precedente al lunedì 00 UTC corrente."""
    if ultimo is None:
        return True
    return datetime.fromisoformat(ultimo) < ultimo_lunedi_00(adesso)


def apri_posizione(stato: dict, simbolo: str, basis: float, quando: datetime) -> dict:
    stato["posizioni"][simbolo] = {
        "notional": NOTIONAL, "aperta": quando.isoformat(),
        "basis_entrata": round(basis, 6), "funding_incassato": 0.0,
    }
    stato["costi_pagati"] = round(stato.get("costi_pagati", 0.0)
                                  + COSTO_APERTURA * NOTIONAL, 6)
    return {"evento": "apertura", "simbolo": simbolo, "basis": round(basis, 6)}


def chiudi_posizione(stato: dict, simbolo: str, basis_uscita: float) -> dict:
    """PnL del ciclo con la contabilità del backtest: funding + Δbasis − costi."""
    pos = stato["posizioni"].pop(simbolo)
    pnl = (pos["funding_incassato"]
           + (pos["basis_entrata"] - basis_uscita) * NOTIONAL
           - (COSTO_APERTURA + COSTO_CHIUSURA) * NOTIONAL)
    stato["pnl_realizzato"] = round(stato.get("pnl_realizzato", 0.0) + pnl, 6)
    stato["costi_pagati"] = round(stato.get("costi_pagati", 0.0)
                                  + COSTO_CHIUSURA * NOTIONAL, 6)
    return {"evento": "chiusura", "simbolo": simbolo,
            "basis_uscita": round(basis_uscita, 6), "pnl": round(pnl, 6)}


def accredita_funding(stato: dict, simbolo: str,
                      eventi_funding: list[tuple[datetime, float]]) -> float:
    """Somma dei rate (lo short li riceve) sul notional, in USDT di carta.
    eventi_funding = lista di (fundingTime, rate). Il high-water mark
    `ultimo_accredito` viene fissato al MAX fundingTime accreditato
    (revisione branch 2026-07-21): fissarlo a un timestamp pre-fetch
    riaccreditava al ciclo dopo un settlement avvenuto tra cattura e fetch."""
    if simbolo not in stato["posizioni"] or not eventi_funding:
        return 0.0
    incasso = sum(r for _, r in eventi_funding) * NOTIONAL
    mark = max(t for t, _ in eventi_funding)
    pos = stato["posizioni"][simbolo]
    pos["funding_incassato"] = round(pos["funding_incassato"] + incasso, 6)
    pos["ultimo_accredito"] = mark.isoformat()
    stato["funding_totale"] = round(stato.get("funding_totale", 0.0) + incasso, 6)
    return incasso


def carica_stato() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"avvio": datetime.now(timezone.utc).isoformat(), "posizioni": {},
            "pnl_realizzato": 0.0, "funding_totale": 0.0, "costi_pagati": 0.0,
            "ultimo_ribilanciamento": None, "ribilanciamenti": 0}


def salva_stato(stato: dict, eventi: list[dict] | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(stato, indent=1))
    if eventi:
        with LEDGER.open("a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            for e in eventi:
                f.write(json.dumps({"ts": ts, **e}) + "\n")
