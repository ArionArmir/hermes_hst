import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.shared import store
from utils import formatting, ohlc, process_manager
from utils.redis_client import (
    get_heartbeat,
    get_json,
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
def render_scala_confidenza():
    """A: quanto vicino è il sistema a tradare — la domanda quotidiana del
    forward. Barre monocolore (una sola misura), tacca = massimo di oggi,
    linea tratteggiata = soglia. Descrive, non suggerisce."""
    import altair as alt

    soglia = (get_trading_config() or {}).get("ml_confidence_threshold", 0.50)
    righe = []
    for symbol in _get_symbols():
        d = get_json(f"ml_conf_{symbol}")
        if d:
            righe.append({"Simbolo": symbol.replace("USDT", ""),
                          "conf": d.get("conf", 0.0), "max_oggi": d.get("max_oggi", 0.0)})
    if not righe:
        st.info("Telemetria ml_conf_* non ancora disponibile (inference in avvio?)")
        return
    df = pd.DataFrame(righe)
    dominio = [0, max(0.6, soglia + 0.1)]
    base = alt.Chart(df).encode(y=alt.Y("Simbolo:N", sort="-x", title=None))
    barre = base.mark_bar(height=12, cornerRadiusEnd=4).encode(
        x=alt.X("conf:Q", scale=alt.Scale(domain=dominio), title="confidenza"),
        tooltip=[alt.Tooltip("Simbolo:N"),
                 alt.Tooltip("conf:Q", format=".3f", title="adesso"),
                 alt.Tooltip("max_oggi:Q", format=".3f", title="max oggi")])
    tacche = base.mark_tick(thickness=2, size=16, color="#8a8a8a").encode(
        x=alt.X("max_oggi:Q"))
    regola = (alt.Chart(pd.DataFrame({"soglia": [soglia]}))
              .mark_rule(strokeDash=[4, 3], color="#8a8a8a")
              .encode(x="soglia:Q"))
    st.altair_chart((barre + tacche + regola).properties(height=220), width="stretch")
    st.caption(f"Barra = confidenza attuale · tacca = massimo di oggi · linea = "
               f"soglia {soglia:.2f}. Sotto la linea il motore non apre: la "
               "distanza dalla soglia È la notizia.")


@st.cache_data(ttl="5m")
def _stats_mercato() -> dict:
    """Le parti lente del nastro: base 24h, percentile di volatilità sui 30
    giorni, liquidazioni dell'ultima ora dal nostro recorder."""
    from src.liquidations.stats import DIR_BINANCE

    liq = {}
    f = DIR_BINANCE / f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d}.parquet"
    if f.exists():
        eventi = pd.read_parquet(f)
        eventi = eventi[eventi["ts"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1)]
        for symbol, g in eventi.groupby("symbol"):
            liq[symbol] = (len(g), float(g["notional_usdt"].mean()))

    out = {}
    for symbol in _get_symbols():
        candele = ohlc.get_candles(symbol)
        if candele.empty or len(candele) < 25:
            continue
        chiusure = candele.set_index("bar_time")["close"].astype(float)
        rendimenti = chiusure.pct_change()
        vol_24h = float(rendimenti.tail(24).std())
        storico = rendimenti.rolling(24).std().dropna().tail(24 * 30)
        out[symbol] = {
            "chiusura_24h_fa": float(chiusure.iloc[-25]),
            "vol_percentile": float((storico < vol_24h).mean()) if len(storico) else None,
            "liq_n": liq.get(symbol, (0, 0.0))[0],
            "liq_media": liq.get(symbol, (0, 0.0))[1],
        }
    return out


@st.fragment(run_every="60s")
def render_nastro_mercato():
    """B: il contesto di mercato senza candele — prezzo, escursione, quanto
    è nervoso il mercato (percentile di volatilità) e le liquidazioni
    dell'ultima ora dal nostro recorder."""
    stats = _stats_mercato()
    if not stats:
        st.info("Candele non ancora disponibili")
        return
    righe = []
    for symbol, s in stats.items():
        prezzo = get_latest_price(symbol) or s["chiusura_24h_fa"]
        righe.append({
            "Simbolo": symbol.replace("USDT", ""),
            "Prezzo": prezzo,
            "Δ24h %": (prezzo / s["chiusura_24h_fa"] - 1) * 100,
            "Volatilità (percentile 30g)": s["vol_percentile"],
            "Liq. 1h": s["liq_n"],
            "Taglia media liq.": s["liq_media"] or None,
        })
    st.dataframe(
        pd.DataFrame(righe), hide_index=True, width="stretch",
        column_config={
            "Prezzo": st.column_config.NumberColumn(format="%.4g"),
            "Δ24h %": st.column_config.NumberColumn(format="%+.1f%%"),
            "Volatilità (percentile 30g)": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="percent"),
            "Taglia media liq.": st.column_config.NumberColumn(format="$%.0f"),
        })
    st.caption("Liquidazioni dal nostro recorder (campione Binance). La colonna "
               "volatilità risponde a \"quanto è nervoso il mercato\" — il lavoro "
               "che il grafico a candele fingeva di fare.")


@st.cache_data(ttl="60s")
def _trade_aperti_forward() -> int:
    return store.count_signals("OPENED")


@st.fragment(run_every="60s")
def render_polso_forward():
    """C: a che punto è l'esperimento — il cruscotto del perché siamo qui."""
    from datetime import date, datetime, timezone

    avvio, lettura = date(2026, 7, 14), date(2027, 1, 19)
    aperti = _trade_aperti_forward()
    trascorsi = (date.today() - avvio).days
    totali = (lettura - avvio).days
    st.progress(min(aperti / 100, 1.0),
                text=f"forward_v1: {aperti}/100 trade · lettura entro il {lettura}")
    eta_val = None
    ts_recenti = [d["ts"] for s in _get_symbols() if (d := get_json(f"ml_conf_{s}"))]
    if ts_recenti:
        ultimo = max(pd.to_datetime(t, format="ISO8601", utc=True) for t in ts_recenti)
        eta_val = (datetime.now(timezone.utc) - ultimo).total_seconds()
    score = get_sentiment_score()
    with st.container(horizontal=True):
        st.metric("Giorni di test", f"{trascorsi}/{totali}", border=True)
        st.metric("Ultima valutazione modello",
                  f"{eta_val:.0f}s fa" if eta_val is not None else "n/d", border=True)
        st.metric("Sentiment v1 (veto sotto −0.50)",
                  f"{score:+.2f}" if score is not None else "n/d", border=True)


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

st.subheader("Scala di confidenza")
render_scala_confidenza()

st.subheader("Nastro di mercato")
render_nastro_mercato()

st.subheader("Polso del forward")
render_polso_forward()

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
    """Ultime 24h dei due aggregati, per il grafico v1-contro-v2. Le due
    serie scrivono a istanti diversi: senza griglia comune ogni colonna è
    piena di NaN alternati e Vega spezza la linea a ogni buco — la v2
    'spariva' così. Ricampionate a 10 minuti, un ffill corto per i bin
    dispari."""
    v1 = store.read_sentiment(limit=3000)
    v1 = v1[v1["asset"] == "aggregate"].copy()
    v1["ts"] = pd.to_datetime(v1["timestamp"], format="ISO8601", utc=True)
    serie = {"v1 (motore)": v1.set_index("ts")["score"]}
    if STORIA_V2.exists():
        righe = [json.loads(r) for r in STORIA_V2.read_text().splitlines()[-300:]]
        v2 = pd.DataFrame([{"ts": r["ts"], "score": r["aggregate"]} for r in righe])
        v2["ts"] = pd.to_datetime(v2["ts"], format="ISO8601", utc=True)
        serie["v2 (ombra)"] = v2.set_index("ts")["score"]
    griglia = {nome: s.resample("10min").mean() for nome, s in serie.items()}
    df = pd.DataFrame(griglia).sort_index().ffill(limit=1)
    return df[df.index >= df.index.max() - pd.Timedelta(hours=24)]


@st.cache_data(ttl="10s")
def _ultimi_segnali() -> pd.DataFrame:
    return store.read_signals(limit=10)


ICONA_SEVERITA = {"allarme": "🔴", "nota": "🟡", "info": "⚪"}


@st.fragment(run_every="60s")
def render_eventi():
    """D: cos'è successo mentre non guardavo — il feed derivato
    dall'osservatore (src/eventi/osservatore.py). Descrive, non suggerisce."""
    from src.eventi.osservatore import leggi_eventi

    eventi = leggi_eventi(15)
    if not eventi:
        st.info("Nessun evento registrato ancora (l'osservatore gira col watchdog)")
        return
    adesso = pd.Timestamp.now(tz="UTC")
    for e in eventi:
        ts = pd.to_datetime(e["ts"], format="ISO8601", utc=True)
        minuti = (adesso - ts).total_seconds() / 60
        quando = (f"{minuti:.0f} min fa" if minuti < 90 else
                  f"{minuti / 60:.0f} ore fa" if minuti < 60 * 36 else
                  f"{ts:%d/%m %H:%M}")
        with st.container(horizontal=True):
            st.markdown(f"{ICONA_SEVERITA.get(e['severita'], '⚪')} **{e['titolo']}**")
            st.caption(f"{e['dettaglio']} · {quando}" if e["dettaglio"] else quando)


st.subheader("Eventi")
render_eventi()

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
