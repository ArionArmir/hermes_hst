"""
Pagina Liquidazioni: salute dei registratori, censura Binance misurata,
regime del mese. Descrive e misura — mai un segnale operativo: le relazioni
col mercato passano dai pre-registri (studio firma, lettura Fase 1).
"""
from datetime import date, datetime, timezone

import streamlit as st

from src.liquidations.stats import (DIR_BINANCE, DIR_BYBIT, carica_daily,
                                    eventi_bybit_oggi, quota_censura,
                                    regime_mensile, salute_registratore)


@st.cache_data(ttl="5m", show_spinner="Leggo i dataset…")
def _stato():
    return (salute_registratore(DIR_BINANCE), salute_registratore(DIR_BYBIT),
            quota_censura(eventi_bybit_oggi()),
            regime_mensile(carica_daily(), f"{date.today():%Y-%m}"))


binance, bybit, censura, regime = _stato()

# ---- salute dei registratori ----------------------------------------------
st.subheader("Salute dei registratori")
for nome, s, nota in (("Binance (campionato: max 1 evento/simbolo/s)", binance,
                       "etichette dello studio firma"),
                      ("Bybit (verità completa, allLiquidation)", bybit,
                       "ground truth per l'eventuale Fase 2")):
    st.markdown(f"**{nome}**")
    if s is None:
        st.info("Nessun dato ancora su disco.")
        continue
    eta = None
    if s["ultimo_evento"] is not None:
        eta = (datetime.now(timezone.utc) - s["ultimo_evento"]).total_seconds() / 60
    with st.container(horizontal=True):
        st.metric("Eventi oggi", s["eventi_oggi"], border=True)
        st.metric("Ultima ora", s["eventi_ultima_ora"], border=True)
        st.metric("Ultimo evento", f"{eta:.0f} min fa" if eta is not None else "—",
                  border=True)
        st.metric("Simboli oggi", s["simboli_oggi"], border=True)
        st.metric("Giorni raccolti", s["giorni_raccolti"], border=True)
    st.caption(nota)

# ---- censura misurata ------------------------------------------------------
st.subheader("La censura di Binance, misurata")
if censura:
    with st.container(horizontal=True):
        st.metric("Eventi Bybit oggi", censura["eventi"], border=True)
        st.metric("Oltre 1/simbolo/secondo", censura["nascosti"], border=True)
        st.metric("Quota che Binance nasconderebbe", f"{censura['quota']:.1%}",
                  border=True)
    st.caption("Misurata sulla verità completa di Bybit: eventi oltre il primo di "
               "ogni simbolo-secondo — quelli che un campionamento alla Binance "
               "taglierebbe. È un minorante: nelle cascate la quota sale.")
else:
    st.info("Serve almeno un giorno di dati Bybit.")

# ---- regime del mese -------------------------------------------------------
st.subheader("Regime del mese")
if regime:
    with st.container(horizontal=True):
        st.metric("Fascia", regime["fascia"], border=True)
        st.metric("Percentile mediano (7 simboli)", f"{regime['mediana']:.0%}",
                  border=True)
        st.metric("Giorni nel mese", regime["giorni_nel_mese"], border=True)
    st.caption("Volume liquidato medio giornaliero del mese, in percentile contro "
               "lo storico Coinalyze dal 2020, per simbolo; poi mediana (le unità "
               "sono quantità di asset: non si sommano tra simboli). Risponde solo "
               "a: il periodo che stiamo registrando è tipico o anomalo?")
else:
    st.info("Aggregato Coinalyze non ancora scaricato (scripts/coinalyze_backfill.py).")

st.divider()
st.caption("Su questa pagina non ci sono grafici liquidazioni-prezzo, di proposito: "
           "guardare relazioni col mercato fuori da un pre-registro è ricerca fatta "
           "con gli occhi. Studio firma: Fase 0 al gate, lettura Fase 1 il 2026-08-03.")
