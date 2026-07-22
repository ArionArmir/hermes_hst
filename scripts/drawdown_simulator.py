"""Simulatore di drawdown in euro — il calcolo da cui discende la taglia.

Per ogni allocazione candidata simula un DCA mensile sulla storia comune
disponibile e mostra: esito finale, episodi di drawdown peggiori, e il mese
per mese dell'episodio peggiore NEI TUOI EURO — perche' "-77%" e' astratto,
"il conto segna X e ne hai versati Y" no.

In piu': gli episodi storici dell'azionario su ~40 anni, per calibrare le
aspettative oltre la finestra corta delle crypto.

Uso:  venv/bin/python scripts/drawdown_simulator.py --mensile 200
      venv/bin/python scripts/drawdown_simulator.py --mensile 300 --iniziale 5000
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.invest.drawdown import drawdown_episodes, load_asset_monthly, simulate_dca

ALLOCAZIONI = {
    "100% azionario (S&P500)": {"SPX": 1.0},
    "90% azionario / 10% BTC": {"SPX": 0.9, "BTCUSDT": 0.1},
    "70% azionario / 30% BTC": {"SPX": 0.7, "BTCUSDT": 0.3},
    "100% BTC": {"BTCUSDT": 1.0},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mensile", type=float, default=200.0)
    ap.add_argument("--iniziale", type=float, default=0.0)
    args = ap.parse_args()

    print(f"DCA simulato: {args.mensile:.0f} €/mese"
          + (f" + {args.iniziale:.0f} € iniziali" if args.iniziale else "")
          + " | prezzi convertiti in EUR\n")

    spx = load_asset_monthly("SPX")
    btc = load_asset_monthly("BTCUSDT")
    prezzi = pd.concat([spx, btc], axis=1).dropna()
    print(f"finestra comune SPX+BTC: {prezzi.index[0]} -> {prezzi.index[-1]} "
          f"({len(prezzi)} mesi)\n")

    print("=" * 100)
    print(f"{'allocazione':28s} {'versato':>9s} {'conto':>10s} {'utile':>9s} "
          f"{'dd peggiore':>12s} {'mesi sotto':>11s}")
    print("-" * 100)
    esiti = {}
    for nome, pesi in ALLOCAZIONI.items():
        out = simulate_dca(prezzi, pesi, args.mensile, args.iniziale)
        eps = drawdown_episodes(out.unit_value, minimo=0.10)
        peggiore = eps[0] if eps else None
        esiti[nome] = (out, peggiore)
        dd_txt = (f"{peggiore.profondita:>11.0%} {peggiore.mesi_sotto:>10d}"
                  if peggiore else f"{'—':>11s} {'—':>10s}")
        print(f"{nome:28s} {out.versato.iloc[-1]:>8.0f}€ {out.conto.iloc[-1]:>9.0f}€ "
              f"{out.conto.iloc[-1]-out.versato.iloc[-1]:>+8.0f}€ {dd_txt}")
    print("=" * 100)

    # ---- il mese per mese dell'episodio peggiore, in euro ----
    print("\nIL TEST DELLO STOMACO — l'episodio peggiore, nei tuoi euro, mese per mese")
    for nome in ("100% azionario (S&P500)", "70% azionario / 30% BTC", "100% BTC"):
        out, ep = esiti[nome]
        if ep is None:
            continue
        fine = ep.recupero or out.conto.index[-1]
        finestra = out.conto.loc[ep.picco:fine]
        vers = out.versato.loc[ep.picco:fine]
        print(f"\n  {nome} — picco {ep.picco}, fondo {ep.fondo} "
              f"({ep.profondita:.0%}), recupero {ep.recupero or 'NON ANCORA'}")
        passo = max(1, len(finestra) // 8)
        for m in list(finestra.index[::passo]) + ([finestra.index[-1]]
                                                  if finestra.index[-1] not in finestra.index[::passo] else []):
            c, v = finestra[m], vers[m]
            print(f"    {m}: versati {v:>7.0f} €  |  il conto segna {c:>7.0f} €  "
                  f"({c-v:+.0f} €)")

    # ---- prospettiva lunga: l'azionario oltre la finestra crypto ----
    print("\n" + "=" * 100)
    print("PROSPETTIVA LUNGA — episodi dell'azionario (S&P500 in USD, storia intera disponibile)")
    print("=" * 100)
    spx_usd = load_asset_monthly("SPX", in_eur=False)
    for e in drawdown_episodes(spx_usd, minimo=0.20)[:5]:
        print(f"  picco {e.picco} -> fondo {e.fondo}: {e.profondita:>5.0%} | "
              f"sotto il picco per {e.mesi_sotto} mesi "
              f"({'recuperato ' + str(e.recupero) if e.recupero else 'ancora aperto'})")
    print("""
LETTURA: la taglia giusta e' quella per cui la riga "il conto segna X" del tuo
mix NON ti farebbe vendere. Se leggendola hai pensato "io li' venderei", scendi
di una riga nella tabella delle allocazioni e rifai il test.""")


if __name__ == "__main__":
    main()
