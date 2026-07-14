#!/usr/bin/env python3
"""
Riepilogo automatico per verificare l'esito di una sessione di trading (es. run overnight).
Uso: python verify_overnight.py
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import redis

REPO_ROOT = Path(__file__).resolve().parent
TRADES_FILE = REPO_ROOT / "data" / "trades_history.csv"
LOGS_DIR = REPO_ROOT / "logs"

LOG_PREFIX = {"engine": "trading", "inference": "inference", "sentiment": "sentiment"}
GAP_THRESHOLD_MINUTES = {"engine": 6, "inference": 3, "sentiment": 20}

TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _print_header(title: str):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def check_trades():
    _print_header("STORICO OPERAZIONI")
    if not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0:
        print("Nessuna operazione registrata in data/trades_history.csv")
        return

    df = pd.read_csv(TRADES_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    print(f"Totale operazioni chiuse: {len(df)}")
    print(f"PnL totale: {df['pnl'].sum():+.2f} USDT")

    print("\nPer motivo di chiusura:")
    print(df.groupby("reason")["pnl"].agg(n="count", pnl_totale="sum"))

    print("\nPer simbolo:")
    print(df.groupby("symbol")["pnl"].agg(n="count", pnl_totale="sum"))

    _print_header("CONTROLLO COERENZA SL/TP (regressione bug SL/TP invertiti)")
    anomalie = []
    for _, row in df.iterrows():
        side, reason = row["side"], row["reason"]
        entry, exit_price = row["entry"], row["exit"]
        if reason == "TAKE_PROFIT":
            ok = exit_price > entry if side == "long" else exit_price < entry
        elif reason == "STOP_LOSS":
            ok = exit_price < entry if side == "long" else exit_price > entry
        else:
            continue
        if not ok:
            anomalie.append(row)

    if anomalie:
        print(f"⚠️  {len(anomalie)} operazioni con direzione entry/exit incoerente col motivo di chiusura:")
        print(pd.DataFrame(anomalie)[["timestamp", "symbol", "side", "entry", "exit", "reason", "pnl"]])
    else:
        print("✅ Nessuna incoerenza: tutte le chiusure TAKE_PROFIT/STOP_LOSS hanno la direzione attesa.")


def check_open_positions():
    _print_header("POSIZIONI ATTUALMENTE APERTE")
    client = redis.Redis(host="localhost", port=6379, decode_responses=True)
    raw = client.get("positions")
    if not raw:
        print("Nessuna posizione aperta")
        return

    positions = json.loads(raw)
    any_open = False
    for symbol, pos in positions.items():
        if not pos.get("is_open"):
            continue
        any_open = True
        side = pos["side"]
        entry, sl, tp = pos["entry_price"], pos["stop_loss"], pos["take_profit"]
        ok = (sl < entry < tp) if side == "long" else (tp < entry < sl)
        flag = "✅" if ok else "⚠️ SL/TP INCOERENTI"
        print(f"{symbol} {side.upper()}: entry={entry:.2f} sl={sl:.2f} tp={tp:.2f} {flag}")
    if not any_open:
        print("Nessuna posizione aperta")


def check_heartbeats():
    _print_header("STATO PROCESSI (heartbeat attuale)")
    client = redis.Redis(host="localhost", port=6379, decode_responses=True)
    now = datetime.now(timezone.utc)
    for service in ("engine", "inference", "sentiment"):
        raw = client.get(f"heartbeat_{service}")
        if not raw:
            print(f"{service}: nessun heartbeat trovato (processo mai avviato con il nuovo codice?)")
            continue
        age = (now - datetime.fromisoformat(raw)).total_seconds()
        stato = "🟢" if age < 30 else "🟡"
        print(f"{stato} {service}: ultimo heartbeat {age:.0f}s fa")


def check_log_gaps():
    _print_header("BUCHI NEI LOG (possibili blocchi/crash notturni)")
    print("Nota: rileva solo buchi nel file di log odierno, non l'intera notte se il file è ruotato.\n")
    for service, prefix in LOG_PREFIX.items():
        candidates = sorted(LOGS_DIR.glob(f"{prefix}_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].log"))
        if not candidates:
            print(f"{service}: nessun file di log trovato")
            continue
        path = candidates[-1]

        timestamps = []
        with open(path, "r", errors="replace") as f:
            for line in f:
                m = TIMESTAMP_RE.match(line)
                if m:
                    timestamps.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))

        threshold = GAP_THRESHOLD_MINUTES[service]
        gaps = [
            (prev, curr, (curr - prev).total_seconds() / 60)
            for prev, curr in zip(timestamps, timestamps[1:])
            if (curr - prev).total_seconds() / 60 > threshold
        ]

        if gaps:
            print(f"{service} ({path.name}): {len(gaps)} buco/i > {threshold} min:")
            for prev, curr, delta in gaps:
                print(f"  {prev} → {curr}  ({delta:.1f} min)")
        else:
            print(f"{service} ({path.name}): nessun buco > {threshold} min ✅")


if __name__ == "__main__":
    check_trades()
    check_open_positions()
    check_heartbeats()
    check_log_gaps()
    print("\nFatto.\n")
