"""
Pagina Analisi: equity curve e PnL dal db SQLite, storia completa delle
decisioni sui segnali (inclusi gli scarti e il perché — la risposta a
"perché il bot non sta tradando?" senza grep nei log), sentiment storico
per asset e stato del modello champion.
"""
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from utils import formatting
from src.shared import store
from src.shared.features import FEATURE_COLS

MODEL_PATH = "config/models/champion.pkl"

# Etichette leggibili per gli esiti della tabella signals
OUTCOME_LABELS = {
    "OPENED": "🟢 aperta",
    "REVERSED": "🔄 invertita",
    "CLOSE": "🚪 chiusura",
    "LOW_CONFIDENCE": "🤏 confidenza bassa",
    "SENTIMENT_VETO": "📰 veto sentiment",
    "ENTRY_COOLDOWN": "⏳ cooldown ingresso",
    "REVERSE_COOLDOWN": "⏳ cooldown reverse",
    "REVERSE_HYSTERESIS": "⏳ isteresi reverse",
    "ALREADY_OPEN": "↔️ già aperta",
    "PATTERN_REJECT": "📊 veto pattern",
    "NO_PRICE": "❓ prezzo assente",
    "NO_CAPITAL": "💸 capitale insufficiente",
    "EXPOSURE_CAP": "🧱 cap esposizione",
}


# ---------- Equity e trade ----------

trades = formatting.load_trades()

st.subheader("Equity e risultati")
if trades.empty:
    st.info("Nessun trade registrato finora: le metriche compariranno alla prima chiusura.")
else:
    fees_total = float(trades["fees"].sum()) if "fees" in trades else 0.0
    pnl_total = float(trades["pnl"].sum())
    hit_rate = float((trades["pnl"] > 0).mean())

    with st.container(horizontal=True):
        st.metric("Trade chiusi", f"{len(trades)}", border=True)
        st.metric("PnL netto totale", f"{pnl_total:+,.2f} USDT", border=True)
        st.metric("Fee pagate", f"{fees_total:,.2f} USDT", border=True)
        st.metric("Hit rate", f"{hit_rate:.0%}", border=True)

    equity = trades[["timestamp"]].copy()
    if "capital_after" in trades and trades["capital_after"].notna().any():
        equity["capitale"] = trades["capital_after"]
    else:
        equity["capitale"] = formatting.CAPITALE_INIZIALE + trades["pnl"].cumsum()

    col_equity, col_symbols = st.columns((3, 2))
    with col_equity:
        with st.container(border=True):
            st.markdown("**Equity curve**")
            st.line_chart(equity, x="timestamp", y="capitale", x_label="", y_label="USDT", height=280)
    with col_symbols:
        with st.container(border=True):
            st.markdown("**PnL netto per simbolo**")
            by_symbol = trades.groupby("symbol", as_index=False)["pnl"].sum()
            st.bar_chart(by_symbol, x="pnl", y="symbol", horizontal=True,
                         x_label="USDT", y_label="", height=280)

    with st.expander(f"Tutti i trade ({len(trades)})"):
        st.dataframe(
            trades.sort_values("timestamp", ascending=False),
            hide_index=True,
            column_config={
                "id": None,
                "timestamp": st.column_config.DatetimeColumn("Quando", format="DD/MM HH:mm:ss"),
                "pnl": st.column_config.NumberColumn("PnL", format="%.2f"),
                "pnl_gross": st.column_config.NumberColumn("PnL lordo", format="%.2f"),
                "fees": st.column_config.NumberColumn("Fee", format="%.3f"),
                "capital_after": st.column_config.NumberColumn("Capitale", format="%.2f"),
            },
        )

st.divider()


# ---------- Decisioni sui segnali ----------

@st.fragment(run_every="10s")
def render_signal_decisions():
    st.subheader("Decisioni sui segnali")
    signals = store.read_signals(limit=500)
    if signals.empty:
        st.info("Nessuna decisione registrata: l'engine scrive qui ogni segnale ML "
                "ricevuto, compresi quelli scartati e il motivo.")
        return

    signals["timestamp"] = pd.to_datetime(signals["timestamp"])
    signals["esito"] = signals["outcome"].map(lambda o: OUTCOME_LABELS.get(o, o))

    counts = signals["outcome"].value_counts()
    st.caption(" · ".join(f"{OUTCOME_LABELS.get(o, o)}: **{c}**" for o, c in counts.items()))

    options = list(counts.index)
    selected = st.pills("Filtra per esito", options, selection_mode="multi",
                        default=options, key="signal_outcome_filter",
                        format_func=lambda o: OUTCOME_LABELS.get(o, o))
    view = signals[signals["outcome"].isin(selected or options)]

    st.dataframe(
        view[["timestamp", "symbol", "action", "confidence", "weighted_confidence", "esito", "detail"]],
        hide_index=True,
        height=380,
        column_config={
            "timestamp": st.column_config.DatetimeColumn("Quando", format="DD/MM HH:mm:ss"),
            "symbol": "Simbolo",
            "action": "Azione",
            "confidence": st.column_config.NumberColumn("Conf. ML", format="%.2f"),
            "weighted_confidence": st.column_config.NumberColumn("Conf. pesata", format="%.2f"),
            "esito": "Esito",
            "detail": "Dettaglio",
        },
    )


render_signal_decisions()

st.divider()


# ---------- Sentiment storico ----------

st.subheader("Sentiment storico")
sentiment = store.read_sentiment(limit=3000)
if sentiment.empty:
    st.info("Nessun dato di sentiment nel database ancora.")
else:
    sentiment["timestamp"] = pd.to_datetime(sentiment["timestamp"])
    sentiment = sentiment.sort_values("timestamp")
    show_aggregate = st.toggle("Mostra solo l'aggregato", value=False)
    view = sentiment[sentiment["asset"] == "aggregate"] if show_aggregate \
        else sentiment[sentiment["asset"] != "aggregate"]
    st.line_chart(view, x="timestamp", y="score", color="asset",
                  x_label="", y_label="sentiment", height=260)

st.divider()


# ---------- Modello ----------

@st.cache_resource(ttl="10m")
def _load_champion(mtime: float):
    """mtime nella chiave di cache: un retraining invalida automaticamente."""
    import joblib
    return joblib.load(MODEL_PATH)


st.subheader("Modello champion")
if not os.path.exists(MODEL_PATH):
    st.warning("Nessun modello champion trovato.")
else:
    mtime = os.path.getmtime(MODEL_PATH)
    trained_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
    try:
        model = _load_champion(mtime)
        trained_names = list(model.get_booster().feature_names or [])
        compatible = trained_names == FEATURE_COLS
        col_info, col_importance = st.columns((1, 2))
        with col_info:
            st.metric("Ultimo training", trained_at.strftime("%d/%m/%Y %H:%M UTC"), border=True)
            st.metric("Feature / classi", f"{len(trained_names)} / {len(model.classes_)}", border=True)
            if compatible:
                st.success("Compatibile con le feature correnti", icon=":material/check_circle:")
            else:
                st.error("NON compatibile: rilanciare train_all_models.py", icon=":material/error:")
        with col_importance:
            with st.container(border=True):
                st.markdown("**Importanza delle feature**")
                importance = pd.DataFrame({
                    "feature": trained_names,
                    "importanza": model.feature_importances_,
                }).sort_values("importanza")
                st.bar_chart(importance, x="importanza", y="feature", horizontal=True,
                             x_label="", y_label="", height=320)
    except Exception as e:
        st.error(f"Impossibile leggere il modello: {e}")
