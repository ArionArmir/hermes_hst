"""Statistiche descrittive sui dataset liquidazioni — per dashboard e
rapporto mensile. Descrive e misura, mai un segnale: le relazioni col
mercato passano dai pre-registri (docs/PRE_REGISTRO_FIRMA_LIQUIDAZIONI.md).

Nota sulle unità del regime: l'aggregato Coinalyze è in QUANTITÀ di asset
base (misurato, non da doc), quindi non si somma tra simboli — il regime si
calcola come percentile per simbolo contro il suo storico, poi mediana dei
percentili. Stesso schema del funding mediano del semaforo carry.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
DIR_BINANCE = _ROOT / "data" / "liquidations"
DIR_BYBIT = _ROOT / "data" / "liquidations_bybit"
DAILY_COINALYZE = _ROOT / "data" / "liquidations_aggregate" / "coinalyze_daily.parquet"

# fasce sul percentile mediano (dichiarate qui, come i bin 2%/8% del carry)
FASCIA_BASSA, FASCIA_ALTA = 0.20, 0.80


def salute_registratore(cartella: Path, adesso: datetime | None = None) -> dict | None:
    """Stato di un recorder: eventi oggi/ultima ora, ultimo evento, copertura.
    None se il dataset non esiste ancora."""
    adesso = adesso or datetime.now(timezone.utc)
    files = sorted(cartella.glob("*.parquet"))
    if not files:
        return None
    oggi = cartella / f"{adesso:%Y-%m-%d}.parquet"
    df = pd.read_parquet(oggi) if oggi.exists() else pd.DataFrame(columns=["ts", "symbol"])
    ultima_ora = df[df["ts"] >= adesso - timedelta(hours=1)] if len(df) else df
    return {
        "giorni_raccolti": len(files),
        "eventi_oggi": len(df),
        "eventi_ultima_ora": len(ultima_ora),
        "simboli_oggi": df["symbol"].nunique() if len(df) else 0,
        "ultimo_evento": df["ts"].max() if len(df) else None,
    }


def quota_censura(eventi_bybit: pd.DataFrame) -> dict | None:
    """Quanto nasconderebbe il campionamento alla Binance (max 1 evento per
    simbolo-secondo), misurato sulla verità completa di Bybit: eventi oltre
    il primo di ogni simbolo-secondo. È un minorante onesto della censura
    Binance, senza confrontare venue di taglia diversa tra loro."""
    if eventi_bybit is None or not len(eventi_bybit):
        return None
    secondi = eventi_bybit["ts"].dt.floor("s")
    visibili = eventi_bybit.groupby([secondi, eventi_bybit["symbol"]]).ngroups
    totale = len(eventi_bybit)
    return {"eventi": totale, "nascosti": totale - visibili,
            "quota": (totale - visibili) / totale}


def regime_mensile(daily: pd.DataFrame, mese: str) -> dict | None:
    """Percentile per simbolo del volume liquidato medio giornaliero del mese
    contro il suo intero storico, poi mediana. daily: colonne t, symbol,
    liq_long, liq_short (aggregato Coinalyze)."""
    if daily is None or not len(daily):
        return None
    df = daily.assign(tot=daily["liq_long"] + daily["liq_short"],
                      mese=daily["t"].dt.strftime("%Y-%m"))
    percentili = {}
    for symbol, gruppo in df.groupby("symbol"):
        del_mese = gruppo[gruppo["mese"] == mese]["tot"]
        if not len(del_mese):
            continue
        percentili[symbol] = (gruppo["tot"] < del_mese.mean()).mean()
    if not percentili:
        return None
    mediana = float(pd.Series(percentili).median())
    fascia = ("ELEVATO" if mediana >= FASCIA_ALTA else
              "BASSO" if mediana <= FASCIA_BASSA else "NELLA NORMA")
    # giorni distinti del mese (revisione branch 2026-07-21): il vecchio
    # calcolo divideva le righe del mese per i simboli di TUTTO lo storico,
    # inclusi i delisted assenti nel mese → il conteggio si dimezzava col
    # tempo. I giorni-calendario distinti sono la misura corretta.
    giorni = int(df.loc[df["mese"] == mese, "t"].dt.normalize().nunique())
    return {"percentili": percentili, "mediana": mediana, "fascia": fascia,
            "giorni_nel_mese": giorni}


def carica_daily() -> pd.DataFrame | None:
    return pd.read_parquet(DAILY_COINALYZE) if DAILY_COINALYZE.exists() else None


def eventi_bybit_oggi(adesso: datetime | None = None) -> pd.DataFrame | None:
    adesso = adesso or datetime.now(timezone.utc)
    f = DIR_BYBIT / f"{adesso:%Y-%m-%d}.parquet"
    return pd.read_parquet(f) if f.exists() else None
