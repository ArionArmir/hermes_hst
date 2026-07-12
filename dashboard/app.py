import streamlit as st
import pandas as pd
import redis
import json
import plotly.graph_objects as go
import glob
import os
from datetime import datetime

st.set_page_config(page_title="Hermes Dashboard - Positions", layout="wide", page_icon="📊")
st.title("📊 Project Hermes - Pannello di Controllo Posizioni")

@st.cache_resource
def get_redis():
    return redis.Redis(host='localhost', port=6379, decode_responses=True)

r = get_redis()

# ---- SIDEBAR ----
with st.sidebar:
    st.header("📈 Stato Sistema")
    positions_data = r.get('positions')
    if positions_data:
        positions = json.loads(positions_data)
        st.success(f"✅ Posizioni aperte: {len(positions)}")
    else:
        st.info("📭 Nessuna posizione aperta")

    sentiment = r.get('sentiment_score')
    if sentiment:
        st.metric("🧠 Sentiment Aggregato", f"{float(sentiment):.2f}")
    model_path = r.get('active_model_path')
    if model_path:
        st.caption(f"📂 Modello: {os.path.basename(model_path)}")

    st.caption(f"🔄 Aggiornamento automatico ogni 5 secondi")

# ---- METRICHE PRINCIPALI ----
CAPITALE_INIZIALE = 1000.0

trades_file = "data/trades_history.csv"
if os.path.exists(trades_file) and os.path.getsize(trades_file) > 0:
    try:
        trades_df = pd.read_csv(trades_file)
        trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
        pnl_cumulato = trades_df['pnl'].sum() if not trades_df.empty else 0.0
        capitale_attuale = CAPITALE_INIZIALE + pnl_cumulato
        # Drawdown
        trades_df['cum_pnl'] = trades_df['pnl'].cumsum()
        trades_df['peak'] = trades_df['cum_pnl'].expanding().max()
        if not trades_df.empty:
            max_drawdown = (trades_df['cum_pnl'] - trades_df['peak']).min() / CAPITALE_INIZIALE * 100
        else:
            max_drawdown = 0.0
    except:
        capitale_attuale = CAPITALE_INIZIALE
        max_drawdown = 0.0
else:
    capitale_attuale = CAPITALE_INIZIALE
    max_drawdown = 0.0

col1, col2, col3 = st.columns(3)
col1.metric("💰 Capitale Iniziale", f"{CAPITALE_INIZIALE:.2f} USDT")
col2.metric("📊 Capitale Attuale", f"{capitale_attuale:.2f} USDT", delta=f"{capitale_attuale - CAPITALE_INIZIALE:+.2f}")
col3.metric("🛡️ Drawdown Max", f"{max_drawdown:.2f}%", delta=f"{max_drawdown:.2f}%", delta_color="inverse")

# ---- TABELLA POSIZIONI APERTE ----
st.subheader("📊 Posizioni Aperte")

if positions_data:
    positions = json.loads(positions_data)
    if positions:
        # Preparazione dati per la tabella
        rows = []
        total_pnl = 0.0
        for symbol, pos in positions.items():
            entry = pos.get('entry_price', 0.0)
            quantity = pos.get('quantity', 0.0)
            side = pos.get('side', 'long')
            sl = pos.get('stop_loss', 0.0)
            tp = pos.get('take_profit', 0.0)
            
            # Prezzo corrente da Redis
            current_price = r.get(f"latest_price_{symbol}")
            if current_price:
                current_price = float(current_price)
            else:
                # Fallback: usa entry price se non disponibile
                current_price = entry
            
            # Calcolo PnL
            if side == 'long':
                pnl_current = (current_price - entry) * quantity
                pnl_sl = (sl - entry) * quantity
                pnl_tp = (tp - entry) * quantity
            else:  # short
                pnl_current = (entry - current_price) * quantity
                pnl_sl = (entry - sl) * quantity
                pnl_tp = (entry - tp) * quantity
            
            total_pnl += pnl_current
            
            rows.append({
                "Symbol": symbol,
                "Side": side.upper(),
                "Qty": f"{quantity:.4f}",
                "Entry": f"{entry:.2f}",
                "Current": f"{current_price:.2f}",
                "Stop Loss": f"{sl:.2f}",
                "Take Profit": f"{tp:.2f}",
                "PnL (USDT)": f"{pnl_current:.2f}",
                "PnL a SL": f"{pnl_sl:.2f}",
                "PnL a TP": f"{pnl_tp:.2f}",
                "Rischio/Profitto": f"{abs(pnl_tp/pnl_sl):.2f}" if pnl_sl != 0 else "∞"
            })
        
        df_positions = pd.DataFrame(rows)
        st.dataframe(df_positions, use_container_width=True)
        
        # Metriche di sintesi
        st.subheader("📈 Riepilogo PnL")
        col1, col2 = st.columns(2)
        col1.metric("💰 PnL Totale (corrente)", f"{total_pnl:.2f} USDT")
        # Calcola PnL potenziale a SL e TP
        pnl_at_sl_sum = sum([float(row['PnL a SL']) for row in rows])
        pnl_at_tp_sum = sum([float(row['PnL a TP']) for row in rows])
        col2.metric("📉 PnL a Stop Loss", f"{pnl_at_sl_sum:.2f} USDT", delta=f"{pnl_at_sl_sum:.2f}", delta_color="inverse")
        col3.metric("📈 PnL a Take Profit", f"{pnl_at_tp_sum:.2f} USDT", delta=f"{pnl_at_tp_sum:.2f}")
        
    else:
        st.info("📭 Nessuna posizione aperta")
else:
    st.info("📭 Nessuna posizione aperta")

# ---- ULTIME OPERAZIONI ----
if os.path.exists(trades_file) and os.path.getsize(trades_file) > 0:
    try:
        trades_df = pd.read_csv(trades_file)
        trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
        trades_df = trades_df.sort_values('timestamp', ascending=False)
        st.subheader("📋 Ultime Operazioni")
        st.dataframe(trades_df[['timestamp', 'symbol', 'side', 'entry', 'exit', 'pnl', 'reason']].head(10), use_container_width=True)
    except:
        pass

# ---- TAB DI LOG E SENTIMENT ----
tab1, tab2, tab3 = st.tabs(["📈 Log Trading", "📊 Sentiment", "🧠 ML Signals"])
with tab1:
    log_files = glob.glob("logs/trading_*.log")
    if log_files:
        latest_log = sorted(log_files)[-1]
        with open(latest_log, "r") as f:
            lines = f.readlines()
            for line in lines[-50:]:
                st.code(line.strip())
with tab2:
    try:
        df = pd.read_csv("data/sentiment_history.csv")
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['score'], mode='lines', name='Sentiment'))
        fig.update_layout(height=400, title="Andamento Sentiment")
        st.plotly_chart(fig, use_container_width=True)
    except:
        st.info("Nessun dato sentiment disponibile")
with tab3:
    log_files = glob.glob("logs/inference_*.log")
    if log_files:
        latest_log = sorted(log_files)[-1]
        with open(latest_log, "r") as f:
            lines = f.readlines()
            signals = [l for l in lines if "Segnale ML" in l]
            for signal in signals[-20:]:
                st.code(signal.strip())
    else:
        st.info("Nessun segnale ML disponibile")

# Auto-refresh
st.caption("🔄 Aggiornamento automatico ogni 5 secondi")
st.empty()