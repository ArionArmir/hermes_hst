"""
Pagina Carry: il semaforo del regime, il paper executor e il tripwire.
Descrive e misura — mai un segnale operativo: l'eventuale riattivazione
passa dal protocollo (docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from src.invest.tripwire import CONSECUTIVI_RICHIESTI, carica, consecutivi_correnti
from src.research.carry_monitor import STORICO_CARRY
from utils.redis_client import get_json

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "carry_paper" / "state.json"
LEDGER = ROOT / "data" / "carry_paper" / "ledger.jsonl"


@st.cache_data(ttl="60s")
def _semaforo():
    """Legge il semaforo da Redis (pubblicato dal servizio carry ogni ora):
    lettura istantanea, niente più decine di chiamate REST a Binance al primo
    render — quello rendeva il tab Carry lento a freddo."""
    return get_json("carry_semaforo")


# ---- semaforo -------------------------------------------------------------
st.subheader("Semaforo del regime")
sem = _semaforo()
if sem:
    pct = sem.get("percentile")
    with st.container(horizontal=True):
        st.metric("Funding mediano 30gg (annuo)", f"{sem['mediana']:+.1%}", border=True)
        st.metric("Fascia", sem["fascia"], border=True)
        st.metric("Percentile storico", f"{pct:.0%}" if pct is not None else "—",
                  border=True)
        st.metric("Simboli a funding positivo", f"{sem['positivi']}/{sem['totale']}",
                  border=True)
    st.caption(sem["nota"] + f" · aggiornato {sem['ts'][:16].replace('T', ' ')} UTC")
    if sem.get("basis"):
        with st.container(horizontal=True):
            for sott, b in sem["basis"].items():
                st.metric(f"Basis {sott} trimestrale ({b['giorni']:.0f}gg)",
                          f"{b['basis_annuo']:+.1%}", border=True)
else:
    st.info("Semaforo in attesa del primo ciclo del servizio carry (hermes-carry).")

# ---- tripwire -------------------------------------------------------------
st.subheader("Tripwire di riattivazione")
tw = carica()
n = consecutivi_correnti(tw)
if tw.get("scattato"):
    st.error("**TRIPWIRE SCATTATO** — fascia RICCA per 2 rapporti consecutivi. "
             "Prossimo passo (umano): pre-registro di attivazione.")
else:
    st.progress(n / CONSECUTIVI_RICHIESTI,
                text=f"{n}/{CONSECUTIVI_RICHIESTI} letture mensili RICCO consecutive "
                     "(il controllo avviene col rapporto del 1° del mese)")
if tw.get("storia"):
    st.dataframe(pd.DataFrame(tw["storia"]).rename(columns={
        "mese": "Mese", "fascia": "Fascia", "mediana": "Funding mediano"}),
        hide_index=True, width="content")

# ---- paper executor -------------------------------------------------------
st.subheader("Paper executor (denaro di carta, contabilità del backtest)")
if STATE.exists():
    s = json.loads(STATE.read_text())
    aperto = sum(p["funding_incassato"] for p in s["posizioni"].values())
    with st.container(horizontal=True):
        st.metric("Posizioni aperte", len(s["posizioni"]), border=True)
        st.metric("Funding incassato", f"{s['funding_totale']:+.2f}", border=True)
        st.metric("PnL realizzato", f"{s['pnl_realizzato']:+.2f}", border=True)
        st.metric("Costi pagati", f"{s['costi_pagati']:.2f}", border=True)
        st.metric("Ribilanciamenti", f"{s['ribilanciamenti']}/50", border=True)
    st.caption(f"Attivo dal {s['avvio'][:10]} · 100 USDT di carta per posizione · "
               "lettura della divergenza backtest/live a 26 settimane o 50 ribilanciamenti")

    if s["posizioni"]:
        pos = (pd.DataFrame.from_dict(s["posizioni"], orient="index")
                 .reset_index(names="Simbolo"))
        pos["aperta"] = pos["aperta"].str[:10]
        st.dataframe(
            pos.rename(columns={"basis_entrata": "Basis entrata",
                                "funding_incassato": "Funding incassato",
                                "aperta": "Aperta il", "notional": "Notional"}),
            hide_index=True,
            column_config={
                "Basis entrata": st.column_config.NumberColumn(format="%.4f"),
                "Funding incassato": st.column_config.NumberColumn(format="%.4f"),
            })
    if LEDGER.exists():
        with st.expander("Ultimi eventi del ledger"):
            righe = LEDGER.read_text().strip().splitlines()[-30:]
            st.dataframe(pd.DataFrame([json.loads(r) for r in reversed(righe)]),
                         hide_index=True)
else:
    st.info("Il paper executor non ha ancora scritto lo stato "
            "(servizio hermes-carry: primo ciclo entro un'ora dall'avvio).")

# ---- riferimento storico --------------------------------------------------
with st.container(border=True):
    st.markdown("**Riferimento — carry_v1, netto per anno (misurato)**")
    st.dataframe(pd.DataFrame([STORICO_CARRY]).T.rename(columns={0: "netto"})
                 .style.format({"netto": "{:+.1%}"}), width="content")
    st.caption("Promosso dai criteri pre-registrati; dorme per regime. "
               "Un terzo del rendimento viene dal solo 2021.")
