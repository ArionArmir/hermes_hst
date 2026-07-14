import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import formatting, ohlc, process_manager
from utils.redis_client import (
    get_heartbeat,
    get_latest_price,
    get_positions,
    get_sentiment_by_asset,
    get_sentiment_score,
    get_trading_config,
)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SERVICES = ["engine", "inference", "sentiment"]
STALE_AFTER_SECONDS = {"engine": 20, "inference": 20, "sentiment": 60}
STATUS_ICON = {"ok": "🟢", "stale": "🟡", "down": "🔴"}


def _get_symbols() -> list[str]:
    config = get_trading_config()
    if config and config.get("symbols"):
        return [s.upper() for s in config["symbols"]]
    return DEFAULT_SYMBOLS


@st.fragment(run_every="5s")
def render_process_status():
    with st.container(horizontal=True):
        for service in SERVICES:
            proc_status = process_manager.status(service)
            heartbeat = get_heartbeat(service)
            state = formatting.heartbeat_status(
                heartbeat, proc_status["running"], STALE_AFTER_SECONDS[service]
            )
            with st.container(border=True):
                st.markdown(f"**{STATUS_ICON[state]} {service.capitalize()}**")
                st.caption(f"PID: {proc_status['pids'] or '—'}")


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
