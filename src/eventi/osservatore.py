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
            # chiave per-id: due aperture distinte stesso simbolo+azione nella
            # stessa ora hanno lo stesso titolo e la dedup oraria (severità
            # "allarme") scarterebbe la seconda, perdendo un evento reale.
            eventi.append(_evento("trade_forward", "allarme",
                                  f"Trade forward aperto: {r.symbol} {r.action}",
                                  f"confidenza pesata {r.weighted_confidence:.3f}",
                                  ts=r.timestamp,
                                  chiave=f"trade_forward:aperto:{r.id}"))
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
            ts: str | None = None, chiave: str | None = None) -> dict:
    assert severita in SEVERITA
    return {"ts": str(ts) if ts else datetime.now(timezone.utc).isoformat(),
            "tipo": tipo, "severita": severita,
            "titolo": titolo, "dettaglio": dettaglio,
            "chiave": chiave or f"{tipo}:{titolo}"}


def _impronta_dedup(e: dict) -> tuple:
    # granularità per SEVERITÀ (revisione branch, regressione della prima
    # passata): allarme/nota per ORA (le ricorrenze legittime — config drift,
    # cascata — devono restare visibili); info per GIORNO (i segnali scartati
    # ad alta frequenza condividono la chiave e con la granularità oraria
    # inondavano il feed 24×, seppellendo gli allarmi nella finestra di 15).
    granularita = 13 if e.get("severita") in ("allarme", "nota") else 10
    return (e["chiave"], e["ts"][:granularita])


def registra_eventi(eventi: list[dict], path: Path = PATH_EVENTI) -> int:
    """Append con dedup per (chiave, ora). Ritorna quanti scritti."""
    path.parent.mkdir(parents=True, exist_ok=True)
    recenti = set()
    if path.exists():
        for riga in path.read_text().splitlines()[-1000:]:
            try:
                recenti.add(_impronta_dedup(json.loads(riga)))
            except (json.JSONDecodeError, KeyError):
                continue
    scritti = 0
    with open(path, "a") as f:
        for e in eventi:
            imp = _impronta_dedup(e)
            if imp in recenti:
                continue
            f.write(json.dumps(e) + "\n")
            recenti.add(imp)
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
        try:
            cursori = json.loads(PATH_CURSORI.read_text())
        except (json.JSONDecodeError, ValueError) as e:
            # un cursori.json troncato (crash a metà scrittura) non deve
            # bloccare per sempre l'osservatore: si riparte da zero (il dedup di
            # registra_eventi assorbe le poche ri-emissioni) e la scrittura
            # atomica sotto ripristina il file sano.
            print(f"[eventi] cursori.json illeggibile, riparto da zero: {e}")
            cursori = {}

    eventi = []
    # letture incrementali per id crescente dal cursore (revisione branch
    # 2026-07-21): avanzano contigue, non saltano un arretrato oltre il limite
    cur_sig = cursori.get("signals", 0)
    segnali = store.read_signals_since(cur_sig)
    da_signals, cursori["signals"] = nuovi_da_signals(segnali, cur_sig)
    cur_tr = cursori.get("trades", 0)
    trades = store.read_trades_since(cur_tr)
    da_trades, cursori["trades"] = nuovi_da_trades(trades, cur_tr)
    righe_ledger = (LEDGER_CARRY.read_text().splitlines()
                    if LEDGER_CARRY.exists() else [])
    da_ledger, cursori["ledger"] = nuovi_da_ledger(righe_ledger, cursori.get("ledger", 0))
    from src.eventi.cascate import eventi_cascata
    eventi = (da_signals + da_trades + da_ledger
              + eventi_watchdog(nuovi_allarmi, rientrati) + eventi_cascata())
    # fonti di rete: ognuna isolata — una fonte muta non azzittisce le altre
    from src.eventi.annunci import check_annunci, check_depeg
    for check in (lambda: check_annunci(cursori), check_depeg):
        try:
            eventi += check()
        except Exception as e:
            print(f"[eventi] fonte non disponibile (non bloccante): {e}")

    scritti = registra_eventi(eventi)
    DIR_EVENTI.mkdir(parents=True, exist_ok=True)
    # scrittura atomica: un crash a metà write_text lasciava un cursori.json
    # troncato che poi bloccava l'osservatore per sempre (json.loads in errore)
    tmp = PATH_CURSORI.with_name(PATH_CURSORI.name + ".tmp")
    tmp.write_text(json.dumps(cursori))
    tmp.replace(PATH_CURSORI)
    return scritti
