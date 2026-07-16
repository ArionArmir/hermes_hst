"""Holdout sigillato e registro dei tentativi.

PERCHÉ ESISTE QUESTO MODULO
Un backtest misura `edge_vero + rumore`. Il rumore appartiene a QUELLO
specifico pezzo di storia e non si ripeterà. Scegliendo il massimo fra N
configurazioni si seleziona anche il rumore favorevole, che dal vivo non si
ripresenta: il numero letto è sistematicamente gonfiato. Con edge vero +50 e
6 tentativi, la vincitrice si legge +206 (4.1x). Con edge vero ZERO e 30
tentativi, la vincitrice si legge +254 — cioè il risultato migliore ottenuto
il 2026-07-16 su questi dati (simulazioni 100k in scratchpad).

Un holdout non guardato riporta il valore vero, perché il suo rumore non ha
partecipato alla scelta. Ma vale UNA CARTUCCIA SOLA: riscegliendo il meglio
di 6 sull'holdout, un edge vero di 0 si rilegge +156. Per questo è diviso in
lotti indipendenti: ognuno è un colpo, e quando finiscono non esistono altri
dati puliti (i 22 simboli già scaricati sono bruciati, e validare in avanti
costa 5-85 anni di paper trading).

Il modulo fa due cose che a mano non reggono:
 1) impedisce l'accesso accidentale ai dati sigillati durante la ricerca;
 2) tiene il conto dei tentativi — senza il quale il Deflated Sharpe Ratio
    non è calcolabile e la correzione per test multipli è impossibile.
Il punto 2 è quello che di solito si salta, ed è quello che rende valido il resto.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

_ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_DIR = _ROOT / "data" / "holdout"
MANIFEST_PATH = _ROOT / "config" / "holdout.yaml"
# In docs/ e non in data/: data/ è in .gitignore, e un registro dei tentativi
# non versionato sparisce al primo `rm -rf data/`, portandosi via il conteggio
# senza cui il Deflated Sharpe non è calcolabile. È un quaderno di
# laboratorio, non un dato derivato.
REGISTRY_PATH = _ROOT / "docs" / "experiment_registry.jsonl"


class HoldoutViolation(RuntimeError):
    """La ricerca ha toccato dati sigillati: il risultato non è valido."""


def _manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise HoldoutViolation(
            f"Nessun sigillo in {MANIFEST_PATH}. Eseguire scripts/seal_holdout.py."
        )
    with open(MANIFEST_PATH) as f:
        return yaml.safe_load(f)


def lots() -> list[str]:
    """Nomi dei lotti, dal più recente sigillato al primo."""
    return sorted(_manifest()["lotti"].keys())


def lot_status(lotto: str) -> str:
    """'SIGILLATO' (cartuccia carica) o 'APERTO' (spesa)."""
    man = _manifest()
    if lotto not in man["lotti"]:
        raise HoldoutViolation(f"Lotto inesistente: {lotto}. Disponibili: {lots()}")
    return man["lotti"][lotto]["stato"]


def sealed_lots() -> list[str]:
    """Lotti ancora spendibili. Vuoto = niente più validazione pulita possibile."""
    return [l for l in lots() if lot_status(l) == "SIGILLATO"]


def sealed_symbols(lotto: str | None = None) -> list[str]:
    """Simboli dell'holdout: di un lotto, o di tutti.

    Sempre vietati in ricerca, anche quelli di un lotto già aperto: un lotto
    speso non torna pulito, e usarlo per cercare e poi citarne il risultato
    come validazione sarebbe riciclaggio.
    """
    man = _manifest()
    nomi = [lotto] if lotto else list(man["lotti"])
    if lotto and lotto not in man["lotti"]:
        raise HoldoutViolation(f"Lotto inesistente: {lotto}. Disponibili: {lots()}")
    return sorted(s for n in nomi for s in man["lotti"][n]["simboli"])


def assert_research_allowed(symbols) -> None:
    """Da chiamare in testa a ogni script di ricerca/tuning.

    Il fallimento silenzioso qui costerebbe l'unica risorsa non rinnovabile del
    progetto, quindi si solleva invece di avvisare.
    """
    contaminati = sorted(set(symbols) & set(sealed_symbols()))
    if contaminati:
        raise HoldoutViolation(
            f"Ricerca su simboli dell'HOLDOUT: {contaminati}. "
            "Sono l'unica fonte pulita rimasta: usarli qui li distrugge. "
            f"Per la validazione finale usare open_seal(). Lotti carichi: {sealed_lots()}"
        )


def record_trial(ipotesi: str, config: dict, risultato: dict) -> None:
    """Registra UN tentativo di ricerca (append-only).

    Va chiamato per OGNI configurazione provata, incluse quelle che perdono:
    il Deflated Sharpe richiede il numero totale di tentativi, e contare solo
    i vincitori è esattamente il modo in cui si ottiene una correzione che non
    corregge nulla.
    """
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    riga = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        "ipotesi": ipotesi,
        "config": config,
        "risultato": risultato,
    }
    with open(REGISTRY_PATH, "a") as f:
        f.write(json.dumps(riga, default=str) + "\n")


def count_trials(ipotesi: str | None = None) -> int:
    """Tentativi registrati, in totale o per famiglia di ipotesi."""
    if not REGISTRY_PATH.exists():
        return 0
    n = 0
    with open(REGISTRY_PATH) as f:
        for riga in f:
            if not riga.strip():
                continue
            if ipotesi is None or json.loads(riga).get("ipotesi") == ipotesi:
                n += 1
    return n


def deflated_sharpe_ratio(pnl_per_trade, n_trials: int) -> float:
    """Probabilità che lo Sharpe osservato rifletta un edge REALE e non la
    fortuna di aver scelto il migliore fra `n_trials` tentativi.

    Bailey & López de Prado (2014). Confronta lo Sharpe osservato con quello
    che il MIGLIORE di `n_trials` strategie a edge nullo otterrebbe per puro
    rumore, tenendo conto di asimmetria e code della distribuzione dei trade.

    <90% = non promuovibile. Il candidato del 2026-07-16 (soglia 0.50, 880
    trade, 6 tentativi dichiarati) dava 51.5%: testa o croce.
    """
    x = np.asarray(pnl_per_trade, dtype=float)
    n = len(x)
    if n < 2 or x.std(ddof=1) == 0:
        return 0.0
    sr = x.mean() / x.std(ddof=1)

    # Sharpe atteso dal massimo di n_trials estrazioni a edge nullo
    if n_trials < 2:
        sr_atteso_max = 0.0
    else:
        emc = 0.5772156649
        ln_t = np.log(n_trials)
        sr_atteso_max = (
            np.sqrt(2 * ln_t) * (1 - emc / (2 * ln_t)) + emc / np.sqrt(2 * ln_t)
        ) / np.sqrt(n)

    skew = stats.skew(x)
    kurt = stats.kurtosis(x, fisher=False)
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    if not np.isfinite(denom) or denom <= 0:
        return 0.0
    return float(stats.norm.cdf((sr - sr_atteso_max) * np.sqrt(n - 1) / denom))


def open_seal(lotto: str, ipotesi: str, n_trials: int, motivazione: str,
              acknowledge_burned: bool = False) -> dict[str, pd.DataFrame]:
    """Apre UN lotto per validare UN candidato già congelato. Irreversibile.

    `n_trials` è il numero di configurazioni provate per arrivare al candidato:
    serve a deflazionare il risultato con deflated_sharpe_ratio(). Dichiararlo
    basso non rende l'edge più reale, rende solo la stima sbagliata — sul
    candidato del 2026-07-16, dichiarare 1 tentativo invece dei 41 veri
    trasformava un DSR del 21.4% in 97.3%.

    L'apertura viene registrata nel manifesto e nel registro. Riaprire un lotto
    speso richiede `acknowledge_burned=True`: non è un lucchetto tecnico — è lì
    perché non possa capitare per distrazione.
    """
    man = _manifest()
    if lotto not in man["lotti"]:
        raise HoldoutViolation(f"Lotto inesistente: {lotto}. Disponibili: {lots()}")
    info = man["lotti"][lotto]

    if info["stato"] != "SIGILLATO" and not acknowledge_burned:
        carichi = sealed_lots()
        raise HoldoutViolation(
            f"Lotto {lotto} GIÀ APERTO da: {info['aperto_da']}. Il suo valore "
            "statistico è speso: riusarlo per una seconda selezione lo rende un "
            "set di ricerca (edge vero 0 -> si rilegge +156). "
            + (f"Usare invece un lotto carico: {carichi}." if carichi else
               "NESSUN lotto carico rimasto: non esistono più dati puliti, "
               "servono mesi di paper trading in avanti.")
            + " Per procedere consapevolmente: acknowledge_burned=True."
        )

    dati = {}
    for sym in info["simboli"]:
        path = HOLDOUT_DIR / f"{sym}_1h.parquet"
        if not path.exists():
            raise HoldoutViolation(f"Dato sigillato mancante: {path}")
        dati[sym] = pd.read_parquet(path)

    info["stato"] = "APERTO"
    info["aperto_da"] = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        "ipotesi": ipotesi,
        "tentativi_dichiarati": n_trials,
        "motivazione": motivazione,
    }
    with open(MANIFEST_PATH, "w") as f:
        yaml.safe_dump(man, f, sort_keys=False, allow_unicode=True)
    record_trial("APERTURA_HOLDOUT", {"lotto": lotto, "ipotesi": ipotesi,
                                      "n_trials": n_trials},
                 {"motivazione": motivazione})
    return dati
