import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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


@st.fragment(run_every="5s")
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


@st.fragment(run_every="5s")
def render_kpis():
    trades_df = formatting.load_trades_history()
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


@st.fragment(run_every="15s")
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
    trades_df = formatting.load_trades_history()
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

st.subheader("Sentiment e segnali")
tab_sentiment, tab_signals = st.tabs(["Sentiment", "Segnali ML"])
with tab_sentiment:
    score = get_sentiment_score()
    by_asset = get_sentiment_by_asset()
    st.metric("Sentiment aggregato", f"{score:.2f}" if score is not None else "n/d")
    if by_asset:
        st.dataframe(pd.DataFrame([by_asset]), hide_index=True, width="stretch")
with tab_signals:
    log_text = process_manager.tail_log("inference", n=200)
    signals = [line for line in log_text.splitlines() if "Segnale ML" in line]
    if signals:
        for line in signals[-20:]:
            st.code(line)
    else:
        st.info("Nessun segnale ML disponibile")
