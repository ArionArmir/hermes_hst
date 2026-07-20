"""Osservatore degli eventi notevoli — il "cos'è successo mentre non guardavo".

Architettura a OSSERVATORE, non a bus: deriva gli eventi dagli artefatti che
i servizi già producono (tabelle, ledger, transizioni watchdog) senza toccare
nessun processo vivo — il motore resta intatto per clausola forward. Gira in
coda al run del watchdog, ogni minuto.

Store append-only col pattern di casa: data/eventi/eventi.jsonl. Idempotente
via cursori per fonte (data/eventi/cursori.json): un rilancio non duplica.
Il catalogo dei tipi è CHIUSO (docs: piano widget D, fase 1) — aggiungerne
uno è una decisione esplicita, mai un'evoluzione spontanea. Il feed descrive
il passato, non suggerisce mai.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DIR_EVENTI = _ROOT / "data" / "eventi"
PATH_EVENTI = DIR_EVENTI / "eventi.jsonl"
PATH_CURSORI = DIR_EVENTI / "cursori.json"
LEDGER_CARRY = _ROOT / "data" / "carry_paper" / "ledger.jsonl"

SEVERITA = ("info", "nota", "allarme")


# ---- funzioni pure (testate) ----------------------------------------------

def nuovi_da_signals(df, cursore: int) -> tuple[list[dict], int]:
    """Eventi dalle decisioni del motore (tabella signals). Il cursore è il
    massimo id già visto: le righe non sono mai riscritte, solo appese."""
    eventi = []
    if df is None or not len(df):
        return eventi, cursore
    nuove = df[df["id"] > cursore].sort_values("id")
    for r in nuove.itertuples():
        if r.outcome == "OPENED":
            eventi.append(_evento("trade_forward", "allarme",
                                  f"Trade forward aperto: {r.symbol} {r.action}",
                                  f"confidenza pesata {r.weighted_confidence:.3f}",
                                  ts=r.timestamp))
        elif r.outcome == "SENTIMENT_VETO":
            eventi.append(_evento("veto_sentiment", "nota",
                                  f"Veto sentiment su {r.symbol}",
                                  str(r.detail or ""), ts=r.timestamp))
        else:
            eventi.append(_evento("filtro_segnale", "info",
                                  f"Segnale {r.symbol} scartato: {r.outcome}",
                                  str(r.detail or ""), ts=r.timestamp))
    return eventi, int(df["id"].max())


def nuovi_da_trades(df, cursore: int) -> tuple[list[dict], int]:
    """Trade chiusi (tabella trades): con ~1 atteso a settimana, ognuno è
    una notizia."""
    eventi = []
    if df is None or not len(df):
        return eventi, cursore
    nuove = df[df["id"] > cursore].sort_values("id")
    for r in nuove.itertuples():
        eventi.append(_evento("trade_forward", "allarme",
                              f"Trade forward chiuso: {r.symbol} {r.side} "
                              f"→ {r.pnl:+.2f} USDT",
                              f"uscita: {r.reason or 'n/d'}", ts=r.timestamp))
    return eventi, int(df["id"].max())


def nuovi_da_ledger(righe: list[str], cursore: int) -> tuple[list[dict], int]:
    """Ribilanciamenti dal ledger carry (gli accrediti di funding sarebbero
    rumore). Il cursore è il numero di righe già processate: il ledger è
    append-only per costruzione."""
    eventi = []
    for riga in righe[cursore:]:
        try:
            r = json.loads(riga)
        except json.JSONDecodeError:
            continue
        if r.get("evento") == "ribilanciamento":
            eventi.append(_evento("carry", "info",
                                  f"Ribilanciamento carry: {r.get('posizioni', '?')} posizioni",
                                  f"selezionati {r.get('selezionati', '?')}",
                                  ts=r.get("ts")))
    return eventi, len(righe)


def eventi_watchdog(nuovi_allarmi: dict, rientrati: list) -> list[dict]:
    """Le transizioni che il watchdog già calcola: qui diventano memoria.
    La deriva di config/modello ha un tipo suo (l'incidente del 2026-07-20)."""
    eventi = []
    for nome, descrizione in sorted((nuovi_allarmi or {}).items()):
        tipo = "deriva" if nome == "config drift" else "watchdog"
        eventi.append(_evento(tipo, "allarme", f"Allarme: {nome}", str(descrizione)))
    for nome in rientrati or []:
        eventi.append(_evento("watchdog", "info", f"Rientrato: {nome}"))
    return eventi


def _evento(tipo: str, severita: str, titolo: str, dettaglio: str = "",
            ts: str | None = None) -> dict:
    assert severita in SEVERITA
    return {"ts": str(ts) if ts else datetime.now(timezone.utc).isoformat(),
            "tipo": tipo, "severita": severita,
            "titolo": titolo, "dettaglio": dettaglio,
            "chiave": f"{tipo}:{titolo}"}


def registra_eventi(eventi: list[dict], path: Path = PATH_EVENTI) -> int:
    """Append con dedup per chiave sullo stesso giorno (le transizioni
    watchdog non hanno un cursore naturale). Ritorna quanti scritti."""
    path.parent.mkdir(parents=True, exist_ok=True)
    recenti = set()
    if path.exists():
        for riga in path.read_text().splitlines()[-300:]:
            try:
                e = json.loads(riga)
                recenti.add((e["chiave"], e["ts"][:10]))
            except (json.JSONDecodeError, KeyError):
                continue
    scritti = 0
    with open(path, "a") as f:
        for e in eventi:
            if (e["chiave"], e["ts"][:10]) in recenti:
                continue
            f.write(json.dumps(e) + "\n")
            recenti.add((e["chiave"], e["ts"][:10]))
            scritti += 1
    return scritti


def leggi_eventi(n: int = 15, path: Path = PATH_EVENTI) -> list[dict]:
    """Gli ultimi n eventi, dal più recente."""
    if not path.exists():
        return []
    righe = path.read_text().splitlines()[-n:]
    out = []
    for riga in reversed(righe):
        try:
            out.append(json.loads(riga))
        except json.JSONDecodeError:
            continue
    return out


# ---- orchestrazione (chiamata dal watchdog) --------------------------------

def osserva_tutto(nuovi_allarmi: dict | None = None,
                  rientrati: list | None = None) -> int:
    """Un giro su tutte le fonti. Non deve MAI far fallire il watchdog:
    chi chiama ci avvolge in un try/except."""
    from src.shared import store

    cursori = {}
    if PATH_CURSORI.exists():
        cursori = json.loads(PATH_CURSORI.read_text())

    eventi = []
    segnali = store.read_signals(limit=10_000)
    da_signals, cursori["signals"] = nuovi_da_signals(segnali, cursori.get("signals", 0))
    trades = store.read_trades(limit=10_000)
    da_trades, cursori["trades"] = nuovi_da_trades(trades, cursori.get("trades", 0))
    righe_ledger = (LEDGER_CARRY.read_text().splitlines()
                    if LEDGER_CARRY.exists() else [])
    da_ledger, cursori["ledger"] = nuovi_da_ledger(righe_ledger, cursori.get("ledger", 0))
    eventi = da_signals + da_trades + da_ledger + eventi_watchdog(nuovi_allarmi, rientrati)

    scritti = registra_eventi(eventi)
    DIR_EVENTI.mkdir(parents=True, exist_ok=True)
    PATH_CURSORI.write_text(json.dumps(cursori))
    return scritti
