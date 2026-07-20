import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.shared import store
from utils import formatting, ohlc, process_manager
from utils.redis_client import (
    get_heartbeat,
    get_last_tick,
    get_latest_price,
    get_positions,
    get_sentiment_by_asset,
    get_sentiment_score,
    get_trading_config,
)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SERVICES = ["engine", "inference", "sentiment"]
STALE_AFTER_SECONDS = {"engine": 20, "inference": 20, "sentiment": 60}
TICK_STALE_AFTER_SECONDS = 30
STATUS_ICON = {"ok": "🟢", "stale": "🟡", "down": "🔴"}
SERVICES_WITH_TICK_FEED = ("engine", "inference")

# Nello stack Docker i servizi sono container: dentro il container della
# dashboard non esistono PID locali da interrogare (pgrep vedrebbe solo se
# stessa), quindi lo stato viene dai soli heartbeat su Redis.
IN_DOCKER = bool(os.getenv("HERMES_IN_DOCKER"))


def _get_symbols() -> list[str]:
    config = get_trading_config()
    if config and config.get("symbols"):
        return [s.upper() for s in config["symbols"]]
    return DEFAULT_SYMBOLS


@st.fragment(run_every="10s")
def render_process_status():
    with st.container(horizontal=True):
        for service in SERVICES:
            heartbeat = get_heartbeat(service)
            if IN_DOCKER:
                # Heartbeat assente = servizio giù; presente ma vecchio = stale.
                if not heartbeat:
                    state = "down"
                elif formatting.age_seconds(heartbeat) <= STALE_AFTER_SECONDS[service]:
                    state = "ok"
                else:
                    state = "stale"
                running = state != "down"
                origin_label = f"container `{service}`"
            else:
                proc_status = process_manager.status(service)
                state = formatting.heartbeat_status(
                    heartbeat, proc_status["running"], STALE_AFTER_SECONDS[service]
                )
                running = proc_status["running"]
                origin_label = f"PID: {proc_status['pids'] or '—'}"
            with st.container(border=True):
                st.markdown(f"**{STATUS_ICON[state]} {service.capitalize()}**")
                st.caption(origin_label)
                if service in SERVICES_WITH_TICK_FEED:
                    last_tick = get_last_tick(service)
                    if last_tick:
                        tick_age = formatting.age_seconds(last_tick)
                        tick_icon = "🟢" if tick_age <= TICK_STALE_AFTER_SECONDS else "🔴"
                        st.caption(f"{tick_icon} Ultimo tick: {tick_age:.0f}s fa")
                    elif running:
                        st.caption("🔴 Nessun tick ricevuto")


@st.fragment(run_every="10s")
def render_kpis():
    trades_df = formatting.load_trades()
    capitale_attuale, max_drawdown = formatting.compute_capital_and_drawdown(trades_df)
    equity = formatting.equity_curve(trades_df)

    with st.container(horizontal=True):
        st.metric(
            "Capitale iniziale",
            f"{formatting.CAPITALE_INIZIALE:,.2f} USDT",
            border=True,
        )
        st.metric(
            "Capitale attuale",
            f"{capitale_attuale:,.2f} USDT",
            f"{capitale_attuale - formatting.CAPITALE_INIZIALE:+,.2f}",
            border=True,
            chart_data=equity,
            chart_type="line",
        )
        st.metric(
            "Drawdown massimo",
            f"{max_drawdown:.2f}%",
            delta=f"{max_drawdown:.2f}%",
            delta_color="inverse",
            border=True,
        )


@st.fragment(run_every="5s")
def render_positions():
    positions = get_positions()
    st.subheader("Posizioni aperte")
    if not positions:
        st.info("Nessuna posizione aperta")
        return

    rows = []
    for symbol, pos in positions.items():
        current_price = get_latest_price(symbol) or pos.get("entry_price", 0.0)
        rows.append(formatting.compute_position_row(symbol, pos, current_price))

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")
    st.metric("PnL totale corrente", f"{df['PnL (USDT)'].sum():,.2f} USDT")


@st.fragment(run_every="30s")
def render_candles():
    symbols = _get_symbols()
    tabs = st.tabs(symbols, on_change="rerun")
    for tab, symbol in zip(tabs, symbols):
        if tab.open:
            with tab:
                df = ohlc.get_candles(symbol)
                if df.empty:
                    st.info("Nessun dato disponibile ancora")
                    continue
                fig = go.Figure(
                    data=[
                        go.Candlestick(
                            x=df["bar_time"],
                            open=df["open"],
                            high=df["high"],
                            low=df["low"],
                            close=df["close"],
                            name=symbol,
                        )
                    ]
                )
                live_price = get_latest_price(symbol)
                if live_price:
                    fig.add_hline(
                        y=live_price, line_dash="dot", annotation_text=f"Live: {live_price:.2f}"
                    )
                fig.update_layout(height=420, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, width="stretch")


render_process_status()
render_kpis()

col_positions, col_trades = st.columns(2)
with col_positions:
    render_positions()
with col_trades:
    st.subheader("Ultime operazioni")
    trades_df = formatting.load_trades()
    if trades_df.empty:
        st.info("Nessuna operazione registrata")
    else:
        st.dataframe(
            trades_df.sort_values("timestamp", ascending=False).head(10),
            hide_index=True,
            width="stretch",
        )

st.subheader("Prezzi")
render_candles()

STORIA_V2 = Path(__file__).resolve().parents[2] / "data" / "sentiment_v2" / "storia.jsonl"


def _get_sentiment_v2() -> tuple[float | None, dict]:
    """Aggregato e dettaglio per asset della v2 in ombra, dalle sue chiavi."""
    from utils.redis_client import get_client, get_json
    raw = get_client().get("sentiment_v2")
    per_asset = {}
    for asset in ["BTC", "ETH", "SOL", "TRX", "DOGE", "BNB", "XRP"]:
        d = get_json(f"sentiment_v2_{asset.lower()}")
        if d:
            per_asset[asset] = d
    return (float(raw) if raw else None), per_asset


@st.cache_data(ttl="5m")
def _storico_confronto() -> pd.DataFrame:
    """Ultime 24h dei due aggregati, per il grafico v1-contro-v2."""
    v1 = store.read_sentiment(limit=3000)
    v1 = v1[v1["asset"] == "aggregate"].copy()
    v1["ts"] = pd.to_datetime(v1["timestamp"], format="ISO8601", utc=True)
    serie = {"v1 (motore)": v1.set_index("ts")["score"]}
    if STORIA_V2.exists():
        righe = [json.loads(r) for r in STORIA_V2.read_text().splitlines()[-300:]]
        v2 = pd.DataFrame([{"ts": r["ts"], "score": r["aggregate"]} for r in righe])
        v2["ts"] = pd.to_datetime(v2["ts"], format="ISO8601", utc=True)
        serie["v2 (ombra)"] = v2.set_index("ts")["score"]
    df = pd.DataFrame(serie).sort_index()
    return df[df.index >= df.index.max() - pd.Timedelta(hours=24)]


@st.cache_data(ttl="10s")
def _ultimi_segnali() -> pd.DataFrame:
    return store.read_signals(limit=10)


st.subheader("Sentiment e segnali")
tab_sentiment, tab_signals = st.tabs(["Sentiment", "Segnali ML"])
with tab_sentiment:
    score = get_sentiment_score()
    v2_score, v2_asset = _get_sentiment_v2()
    with st.container(horizontal=True):
        st.metric("v1 — aggregato (alimenta il motore)",
                  f"{score:.2f}" if score is not None else "n/d", border=True)
        st.metric("v2 — aggregato (in ombra, non letto dal motore)",
                  f"{v2_score:.2f}" if v2_score is not None else "n/d", border=True)
    by_asset = get_sentiment_by_asset()
    if by_asset or v2_asset:
        righe = []
        for asset in sorted(set(by_asset) | set(v2_asset)):
            d = v2_asset.get(asset, {})
            righe.append({"Asset": asset, "v1": by_asset.get(asset),
                          "v2": d.get("score"), "Stato v2": d.get("stato", "—"),
                          "Notizie nuove": d.get("notizie_nuove")})
        st.dataframe(pd.DataFrame(righe), hide_index=True, width="stretch",
                     column_config={
                         "v1": st.column_config.NumberColumn(format="%.2f"),
                         "v2": st.column_config.NumberColumn(format="%.2f"),
                     })
    confronto = _storico_confronto()
    if len(confronto):
        st.line_chart(confronto, height=220)
    st.caption("La v2 gira in ombra con criteri di accettazione scritti prima "
               "(docs/CRITERI_SENTIMENT_V2.md): confronto il 2026-08-04. Lo zero "
               "della v2 ha sempre uno stato che lo spiega; quello della v1 no — "
               "è uno dei difetti misurati che la v2 deve correggere.")
with tab_signals:
    # Ultime decisioni dal db (tabella signals): la vista completa con
    # filtri è nella pagina Analisi
    signals_df = _ultimi_segnali()
    if signals_df.empty:
        st.info("Nessuna decisione sui segnali registrata ancora")
    else:
        signals_df["timestamp"] = pd.to_datetime(signals_df["timestamp"])
        st.dataframe(
            signals_df[["timestamp", "symbol", "action", "weighted_confidence", "outcome", "detail"]],
            hide_index=True,
            width="stretch",
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Quando", format="DD/MM HH:mm:ss"),
                "weighted_confidence": st.column_config.NumberColumn("Conf. pesata", format="%.2f"),
            },
        )
        st.page_link("app_pages/analysis.py", label="Vista completa nella pagina Analisi",
                     icon=":material/analytics:")
