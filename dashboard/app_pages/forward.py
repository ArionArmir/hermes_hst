"""
Pagina Forward 0.50: l'esperimento in avanti sulla soglia (PRE_REGISTRO_FORWARD).
Mostra il progresso verso la lettura, la telemetria di confidenza e le
decisioni — e ricorda che nessuna lettura intermedia ha potere decisionale.
"""
import json
from datetime import date, datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from src.shared import store
from utils.redis_client import get_client

SOGLIA = 0.50
TRADE_RICHIESTI = 100
DATA_LETTURA = date(2027, 1, 19)
AVVIO = date(2026, 7, 19)

# ---- la carta dell'esperimento -------------------------------------------
with st.container(border=True):
    st.markdown(
        "**Ipotesi** (docs/PRE_REGISTRO_FORWARD.md): la soglia 0.50 — predetta "
        "a priori dai bucket, confine strutturale — ha Sharpe > 0 sui dati "
        "futuri. **Prior dichiarato: ≈ 0.** Unico cambio: soglia 0.55→0.50; "
        "ogni altro ritocco termina l'esperimento.")

aperti = store.count_signals("OPENED")
valutati = store.count_signals()
giorni = (date.today() - AVVIO).days
with st.container(horizontal=True):
    st.metric("Trade aperti", f"{aperti}/{TRADE_RICHIESTI}", border=True)
    st.metric("Giorni di esperimento", giorni, border=True)
    st.metric("Lettura entro", DATA_LETTURA.isoformat(), border=True)
    st.metric("Segnali valutati dai filtri", valutati, border=True)
st.progress(min(1.0, aperti / TRADE_RICHIESTI),
            text=f"verdetto a {TRADE_RICHIESTI} trade o al {DATA_LETTURA} — "
                 "nessuna lettura intermedia ha potere decisionale")

# ---- telemetria di confidenza --------------------------------------------
st.subheader("Telemetria di confidenza (ogni valutazione, anche sotto soglia)")
r = get_client()
righe = []
for chiave in sorted(r.keys("ml_conf_*")):
    d = json.loads(r.get(chiave))
    righe.append({"simbolo": chiave.replace("ml_conf_", ""),
                  "conf. attuale": d["conf"], "massimo di oggi": d["max_oggi"],
                  "p_up": d["p_up"], "p_down": d["p_down"],
                  "aggiornata": d["ts"][11:19]})
if righe:
    df = pd.DataFrame(righe)
    lungo = df.melt(id_vars="simbolo",
                    value_vars=["conf. attuale", "massimo di oggi"],
                    var_name="misura", value_name="confidenza")
    barre = (alt.Chart(lungo).mark_bar()
             .encode(x=alt.X("simbolo:N", title=None),
                     y=alt.Y("confidenza:Q", scale=alt.Scale(domain=[0, 0.6])),
                     xOffset="misura:N", color=alt.Color("misura:N", title=None),
                     tooltip=["simbolo", "misura", "confidenza"]))
    linea = (alt.Chart(pd.DataFrame({"y": [SOGLIA]})).mark_rule(strokeDash=[6, 4])
             .encode(y="y:Q"))
    st.altair_chart(barre + linea, width="stretch")
    st.caption("La linea tratteggiata è la soglia 0.50: un segnale nasce solo "
               "quando una barra la supera. «Massimo di oggi» = quanto ci si è "
               "andati vicini dalla mezzanotte UTC.")
    st.dataframe(df, hide_index=True)
else:
    st.info("Telemetria non ancora disponibile (il servizio inference la "
            "pubblica a ogni ciclo).")

# ---- decisioni ------------------------------------------------------------
st.subheader("Decisioni (segnali sopra soglia e loro esito)")
segnali = store.read_signals(limit=50)          # solo per il display: bounded
if len(segnali):
    st.dataframe(segnali, hide_index=True)
else:
    st.info("Nessun segnale ha ancora superato la soglia — coerente con "
            "l'aspettativa pre-registrata (~3.4 trade/settimana a macchina "
            "accesa; P(zero in una notte) ≈ 80%). Il silenzio qui è il "
            "sistema che funziona: la telemetria sopra mostra che sta "
            "valutando.")
