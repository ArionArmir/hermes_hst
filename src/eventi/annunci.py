"""Annunci Binance (delisting) e sonda depeg — fonti operative del feed eventi.

Il valore non è l'edge ma non essere ciechi: un delisting di una coppia dei
nostri universi (7 del motore, 35 del carry paper) romperebbe l'esperimento
in silenzio; un depeg di stablecoin è l'evento sistemico per eccellenza.
Soglie e cadenze dichiarate qui. Verificato il 2026-07-21: la notice del
2026-07-24 (7 coppie rimosse) non tocca i nostri universi.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
LISTA_ANNUNCI = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
DETTAGLIO_ANNUNCIO = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
CATALOGO_DELISTING = 161
INTERVALLO_ANNUNCI_MIN = 30     # il CMS non si martella ogni minuto
SOGLIA_DEPEG = 0.005            # 0.5% di scostamento dal peg
_H = {"User-Agent": "Mozilla/5.0"}


# ---- funzioni pure (testate) ----------------------------------------------

def estrai_coppie(corpo: str) -> set[str]:
    """Le coppie 'BASE/QUOTE' citate nel corpo di una notice (per il conteggio
    informativo)."""
    return {f"{b}{q}" for b, q in re.findall(r"\b([A-Z0-9]{2,15})/([A-Z0-9]{2,10})\b",
                                             corpo or "")}


def simboli_citati(testo: str, simboli: set) -> set:
    """Quali dei NOSTRI simboli noti sono citati nel testo — sia concatenati
    (BTCUSDT) sia con slash (BTC/USDT). Cerca i simboli noti invece di
    estrarli genericamente: le notice futures Binance li elencano CONCATENATI,
    forma che estrai_coppie (solo BASE/QUOTE) mancava — un delisting di un
    nostro simbolo restava senza allarme (revisione branch 2026-07-21)."""
    su = (testo or "").upper()
    trovati = set()
    for sym in simboli:
        base = sym[:-4] if sym.endswith("USDT") else sym
        quote = sym[len(base):] or "USDT"
        if (re.search(rf"\b{re.escape(sym)}\b", su)
                or re.search(rf"\b{re.escape(base)}/{re.escape(quote)}\b", su)):
            trovati.add(sym)
    return trovati


def eventi_da_annunci(articoli: list[dict], corpi: dict, universi: dict,
                      visti: list) -> tuple[list[dict], list]:
    """Eventi dalle notice non ancora viste. universi: {nome: set di simboli}.
    Notice qualunque = info; notice che tocca un nostro universo = allarme."""
    from src.eventi.osservatore import _evento

    eventi, visti = [], list(visti)
    for art in articoli:
        codice = art.get("code")
        if not codice or codice in visti:
            continue
        visti.append(codice)
        corpo = corpi.get(codice, "")
        # titolo + corpo: le notice spesso nominano i simboli nel solo titolo
        testo = f"{art.get('title', '')} {corpo}"
        coppie = estrai_coppie(corpo)                 # conteggio informativo
        colpiti = {nome: sorted(cit) for nome, simboli in universi.items()
                   if (cit := simboli_citati(testo, simboli))}
        if colpiti:
            e = _evento("delisting", "allarme",
                        f"Delisting tocca i nostri universi: {art.get('title', '')[:60]}",
                        "; ".join(f"{n}: {', '.join(s)}" for n, s in colpiti.items()))
        else:
            e = _evento("delisting", "info",
                        f"Notice Binance: {art.get('title', '')[:70]}",
                        f"{len(coppie)} coppie citate, nessuna dei nostri universi")
        e["chiave"] = f"delisting:{codice}"
        eventi.append(e)
    return eventi, visti


def evento_depeg(prezzo: float, adesso: datetime,
                 soglia: float = SOGLIA_DEPEG) -> dict | None:
    """Scostamento di USDCUSDT dal peg: copre entrambe le stablecoin (se si
    muove USDT, lo scostamento appare uguale e contrario). Dedup per ora:
    un depeg che dura non è spam, un depeg nuovo è una notizia."""
    from src.eventi.osservatore import _evento

    scarto = abs(1.0 - prezzo)
    if scarto < soglia:
        return None
    e = _evento("depeg", "allarme",
                f"Depeg stablecoin: USDCUSDT a {prezzo:.4f}",
                f"scostamento {scarto:.2%} (soglia {soglia:.1%})")
    e["chiave"] = f"depeg:USDC:{adesso:%Y-%m-%d %H}"
    return e


def universi_correnti() -> dict:
    """I simboli da proteggere: i 7 del manifest e le posizioni carry."""
    import yaml
    universi = {}
    manifest = _ROOT / "config" / "forward_manifest.yaml"
    if manifest.exists():
        simboli = yaml.safe_load(manifest.read_text())["config"].get("symbols", [])
        universi["motore"] = {s.upper() for s in simboli}
    stato_carry = _ROOT / "data" / "carry_paper" / "state.json"
    if stato_carry.exists():
        universi["carry"] = set(json.loads(stato_carry.read_text())["posizioni"])
    return universi


# ---- wrapper di rete (chiamati dall'osservatore) ---------------------------

def check_annunci(cursori: dict) -> list[dict]:
    """Ogni INTERVALLO_ANNUNCI_MIN: lista delisting + corpo delle notice
    nuove. Il cursore tiene ultimo check e codici visti."""
    adesso = datetime.now(timezone.utc)
    ultimo = cursori.get("annunci_ultimo_check")
    if ultimo and (adesso - datetime.fromisoformat(ultimo)).total_seconds() < INTERVALLO_ANNUNCI_MIN * 60:
        return []
    cursori["annunci_ultimo_check"] = adesso.isoformat()

    r = requests.get(LISTA_ANNUNCI, params={"type": 1, "catalogId": CATALOGO_DELISTING,
                                            "pageNo": 1, "pageSize": 10},
                     headers=_H, timeout=15)
    r.raise_for_status()
    articoli = r.json()["data"]["catalogs"][0]["articles"]
    visti = cursori.get("annunci_visti", [])
    corpi = {}
    for art in articoli:
        if art.get("code") and art["code"] not in visti:
            det = requests.get(DETTAGLIO_ANNUNCIO, params={"articleCode": art["code"]},
                               headers=_H, timeout=15)
            corpi[art["code"]] = (det.json().get("data") or {}).get("body", "") if det.ok else ""
    eventi, cursori["annunci_visti"] = eventi_da_annunci(
        articoli, corpi, universi_correnti(), visti)
    cursori["annunci_visti"] = cursori["annunci_visti"][-200:]
    return eventi


def check_depeg() -> list[dict]:
    r = requests.get("https://api.binance.com/api/v3/ticker/price",
                     params={"symbol": "USDCUSDT"}, timeout=10)
    r.raise_for_status()
    e = evento_depeg(float(r.json()["price"]), datetime.now(timezone.utc))
    return [e] if e else []
