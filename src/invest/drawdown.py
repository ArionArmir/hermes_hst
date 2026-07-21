"""Simulatore di drawdown in euro — il calcolo che decide la taglia.

La domanda del gioco 2 non è "su cosa" ma "QUANTO": la taglia giusta è quella
che permette di attraversare il drawdown peggiore senza diventare venditori
forzati. Questo modulo risponde mostrando gli episodi peggiori di un
portafoglio DCA nei TUOI euro, mese per mese — perché "-77%" è un numero
astratto, "il conto segna 7.900 € e ne hai versati 12.400" no.

Convenzioni:
- serie mensili (close di fine mese), in EUR quando il cambio è disponibile
- DCA: il versamento compra ai pesi target a inizio mese; nessun
  ribilanciamento attivo (per un DCA in accumulo l'effetto è secondario e
  tenerlo fuori evita di nascondere una scelta dentro la simulazione)
- il drawdown "puro" si misura sull'indice di valore unitario (TWR), non sul
  conto: i versamenti in corso mascherano i cali. Gli euro si leggono sul
  conto, la percentuale sull'unit value.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_ROOT = Path(__file__).resolve().parents[2]
CACHE = _ROOT / "data" / "invest"
SPOT = _ROOT / "data" / "spot"
_UA = {"User-Agent": "Mozilla/5.0"}

# ticker Yahoo per gli asset non-crypto
YAHOO = {"SPX": "%5EGSPC", "EURUSD": "EURUSD%3DX", "VWCE": "VWCE.DE"}
# quotati gia' in EUR: nessuna conversione col cambio
EUR_NATIVI = {"VWCE"}


def _fetch_yahoo_monthly(symbol: str, strict: bool = True) -> pd.Series:
    # period1/period2 espliciti: con range=max Yahoo restituisce mesi
    # CAMPIONATI (168 punti sparsi su 40 anni), che falserebbero il DCA
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO[symbol]}"
           f"?period1=0&period2=9999999999&interval=1mo")
    r = requests.get(url, headers=_UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s").to_period("M")
    close = pd.Series(res["indicators"]["quote"][0]["close"], index=idx, name=symbol)
    close = close.dropna()
    close = close[~close.index.duplicated(keep="last")].sort_index()
    # un buco in mezzo alla serie falserebbe il DCA (mesi senza versamento)
    # senza dirlo: per gli asset ci si ferma. Per l'FX (serve solo alla
    # conversione, e i buchi di Yahoo stanno nei primi anni) si tiene il
    # tratto finale contiguo.
    gaps = np.where(np.diff(close.index.astype("int64")) > 1)[0]
    if len(gaps):
        if strict:
            raise ValueError(f"{symbol}: serie mensile con buchi interni, non simulabile")
        close = close.iloc[gaps[-1] + 1:]
    return close


def _fetch_eurusd_monthly() -> pd.Series:
    """EURUSD da FRED (DEXUSEU, giornaliera): la serie mensile di Yahoo è
    sparsa ovunque e la potatura al tratto contiguo la ridurrebbe a mesi.
    L'aggregazione mensile dell'ultima osservazione del giorno è contigua."""
    import io
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU",
                     timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["data", "usd_per_eur"]
    df["data"] = pd.to_datetime(df["data"])
    df["usd_per_eur"] = pd.to_numeric(df["usd_per_eur"], errors="coerce")
    df = df.dropna()
    return (df.set_index("data")["usd_per_eur"]
              .groupby(lambda t: t.to_period("M")).last().rename("EURUSD"))


def refresh_yahoo_cache(nomi: tuple[str, ...] = ("SPX", "EURUSD", "VWCE")) -> dict:
    """Riscarica e sovrascrive i parquet mensili dei fondi. La dashboard NON
    scarica mai (load_asset_monthly legge il parquet locale): a tenerlo fresco
    ci pensa questo job sul timer mensile — cadenza giusta per dati mensili.
    Senza, il parquet nasceva e non si aggiornava mai (simulazione congelata).
    Tollerante al fallimento per-simbolo: uno che non risponde non blocca gli altri."""
    CACHE.mkdir(parents=True, exist_ok=True)
    esiti = {}
    for name in nomi:
        try:
            s = _fetch_eurusd_monthly() if name == "EURUSD" else _fetch_yahoo_monthly(name)
            s.rename(name).to_frame().to_parquet(CACHE / f"{name}_monthly.parquet")
            esiti[name] = f"{len(s)} mesi → {s.index[-1]}"
        except Exception as e:
            esiti[name] = f"ERRORE: {type(e).__name__}"
    return esiti


def load_asset_monthly(name: str, in_eur: bool = True) -> pd.Series:
    """Serie mensile di un asset: 'SPX' da Yahoo, 'EURUSD' da FRED (cache
    locale, aggiornata dal job mensile refresh_yahoo_cache), altrimenti un
    simbolo crypto dai parquet spot già in casa."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if name in YAHOO:
        cache = CACHE / f"{name}_monthly.parquet"
        if cache.exists():
            s = pd.read_parquet(cache)[name]
            s.index = pd.PeriodIndex(s.index, freq="M")
        else:
            s = (_fetch_eurusd_monthly() if name == "EURUSD"
                 else _fetch_yahoo_monthly(name))
            s.rename(name).to_frame().to_parquet(cache)
    else:
        px = pd.read_parquet(SPOT / f"{name}_1h.parquet").set_index("timestamp")["close"]
        s = px.groupby(px.index.to_period("M")).last().rename(name)

    if in_eur and name != "EURUSD" and name not in EUR_NATIVI:
        fx = load_asset_monthly("EURUSD", in_eur=False)
        comuni = s.index.intersection(fx.index)
        s = s.loc[comuni] / fx.loc[comuni]      # da USD a EUR
    # la divisione fa perdere il nome alla serie, e a valle il concat per
    # colonna lo usa come chiave
    return s.rename(name)


@dataclass
class EsitoDCA:
    conto: pd.Series          # valore del portafoglio, EUR
    versato: pd.Series        # contributi cumulati, EUR
    unit_value: pd.Series     # indice TWR del mix (base 100)


def simulate_dca(prezzi: pd.DataFrame, pesi: dict[str, float],
                 mensile: float, iniziale: float = 0.0) -> EsitoDCA:
    """DCA mensile ai pesi target su serie di prezzo allineate."""
    assert abs(sum(pesi.values()) - 1) < 1e-9, "i pesi devono sommare a 1"
    prezzi = prezzi[list(pesi)].dropna()
    quote = {a: 0.0 for a in pesi}
    conto, versato = [], []
    tot_versato = 0.0
    for i, (mese, riga) in enumerate(prezzi.iterrows()):
        contributo = mensile + (iniziale if i == 0 else 0.0)
        for a, w in pesi.items():
            quote[a] += contributo * w / riga[a]
        tot_versato += contributo
        conto.append(sum(quote[a] * riga[a] for a in pesi))
        versato.append(tot_versato)
    # indice TWR del mix: rendimenti mensili pesati (pesi target costanti)
    rend = (prezzi.pct_change().fillna(0) * pd.Series(pesi)).sum(axis=1)
    unit = 100 * (1 + rend).cumprod()
    return EsitoDCA(pd.Series(conto, index=prezzi.index),
                    pd.Series(versato, index=prezzi.index), unit)


@dataclass
class Episodio:
    picco: pd.Period
    fondo: pd.Period
    profondita: float         # es. -0.55
    recupero: pd.Period | None
    mesi_sotto: int           # dal picco al recupero (o a fine serie)


def drawdown_episodes(unit: pd.Series, minimo: float = 0.15) -> list[Episodio]:
    """Episodi di drawdown dell'indice unitario più profondi di `minimo`,
    ordinati dal peggiore."""
    massimo = unit.cummax()
    dd = unit / massimo - 1
    episodi, in_dd, start = [], False, None
    for i, (t, v) in enumerate(dd.items()):
        if not in_dd and v < 0:
            in_dd, start = True, i - 1 if i else 0
        elif in_dd and v == 0:
            seg = dd.iloc[start:i + 1]
            if seg.min() <= -minimo:
                fondo = seg.idxmin()
                episodi.append(Episodio(dd.index[start], fondo, float(seg.min()),
                                        t, (t - dd.index[start]).n))
            in_dd = False
    if in_dd:
        seg = dd.iloc[start:]
        if seg.min() <= -minimo:
            episodi.append(Episodio(dd.index[start], seg.idxmin(), float(seg.min()),
                                    None, (dd.index[-1] - dd.index[start]).n))
    return sorted(episodi, key=lambda e: e.profondita)
