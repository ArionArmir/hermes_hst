"""Divide il sigillo in due lotti indipendenti da 6 simboli.

PERCHÉ DUE LOTTI
L'holdout è a cartuccia singola: ogni ricerca condotta su di esso lo trasforma
in un set di ricerca e ne annulla il valore (riscegliendo il meglio di 6 su un
holdout, un edge vero di 0 si rilegge +156). Con un lotto solo, il primo test
consuma tutto e il secondo candidato non ha più nulla su cui essere validato —
e altri dati puliti non esistono: i 22 simboli già scaricati sono bruciati e
validare in avanti costa 5-85 anni di paper trading.

Due lotti costano metà potenza per test (6 simboli invece di 12) ma danno due
colpi invece di uno. Con l'incertezza attuale — il DSR del candidato soglia
0.50 sta fra 21.4% (contando 41 tentativi al buio) e 97.3% (fingendone uno) —
avere un secondo colpo vale più della precisione del primo.

CRITERIO DI DIVISIONE
Ordinamento per data di quotazione, poi alternanza. È deterministico,
verificabile, e soprattutto NON guarda i rendimenti: dividere i simboli in base
a come si comportano sarebbe già una selezione, e li brucerebbe entrambi
nell'atto di separarli. L'alternanza per anzianità dà ai due lotti profondità
storica comparabile.

Uso:  venv/bin/python scripts/split_holdout_lots.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

from src.shared.holdout import MANIFEST_PATH


def main():
    with open(MANIFEST_PATH) as f:
        man = yaml.safe_load(f)

    if "lotti" in man:
        logger.error("❌ Sigillo già diviso in lotti. Ridividerlo rimescolerebbe "
                     "simboli fra lotti aperti e sigillati, contaminando i secondi.")
        return 1
    if man.get("stato") != "SIGILLATO":
        logger.error(f"❌ Sigillo in stato {man.get('stato')}: dividere ora "
                     "creerebbe lotti già bruciati spacciati per puliti.")
        return 1

    simboli = man["simboli"]
    # Anzianità di quotazione, non rendimenti
    per_eta = sorted(simboli.items(), key=lambda kv: kv[1]["da"])
    lotti = {"A": dict(per_eta[0::2]), "B": dict(per_eta[1::2])}

    man["criterio_lotti"] = (
        "Ordinamento per data di quotazione e alternanza: deterministico e "
        "indipendente dai rendimenti. Dividere i simboli in base a come si "
        "comportano sarebbe già una selezione e li brucerebbe entrambi. "
        "L'alternanza per anzianità dà ai lotti profondità storica comparabile."
    )
    man["lotti"] = {
        nome: {"stato": "SIGILLATO", "aperto_da": None, "simboli": simb}
        for nome, simb in lotti.items()
    }
    del man["simboli"]
    del man["stato"]
    del man["aperto_da"]

    with open(MANIFEST_PATH, "w") as f:
        yaml.safe_dump(man, f, sort_keys=False, allow_unicode=True)

    for nome, simb in lotti.items():
        barre = sum(s["barre"] for s in simb.values())
        logger.info(f"🔒 lotto {nome}: {len(simb)} simboli, {barre:,} barre")
        for sym, s in simb.items():
            nota = ""
            # EOSUSDT è delistato a metà 2025: nessun dato nell'ultimo anno.
            # Non è un difetto — un holdout di soli sopravvissuti avrebbe
            # survivorship bias — ma va saputo leggendo i risultati del lotto.
            if s["a"] < "2026-01-01":
                nota = f"  ⚠️  delistato il {s['a']}: nessun dato recente"
            logger.info(f"     {sym:12s} {s['barre']:>6,} barre  {s['da']} -> {s['a']}{nota}")

    logger.info("\n✅ Due cartucce indipendenti. open_seal() ora richiede il lotto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
