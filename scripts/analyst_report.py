"""Il rapporto dell'analista — descrive, contestualizza, non prevede mai.

Quattro sezioni: il tuo piano, lo stato del mercato col contesto storico,
i numeri del mese, l'eco dell'IPS. Nei momenti di tempesta rilegge il
protocollo del crollo firmato a mente fredda: il suo mestiere è il behavior
gap, non le previsioni.

Gli acquisti si registrano in data/invest/ledger.csv:
    data,strumento,eur,quote
    2026-08-01,ETF,300,0.061
(strumenti riconosciuti: ETF -> proxy S&P500 in EUR; BTC -> BTCUSDT in EUR.
La valutazione e' un'approssimazione via indice: quella esatta e' del broker.)

Uso:  venv/bin/python scripts/analyst_report.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.invest.analyst import (classifica, contesto_storico, stato_corrente,
                                valuta_ledger)
from src.invest.drawdown import load_asset_monthly

LEDGER = Path(__file__).parent.parent / "data" / "invest" / "ledger.csv"
IPS = "docs/PRE_REGISTRO_INVESTIMENTO.md"
PROXY = {"ETF": "SPX", "BTC": "BTCUSDT"}


def sezione(titolo):
    print("\n" + "=" * 78)
    print(titolo)
    print("=" * 78)


def main():
    print(f"RAPPORTO DELL'ANALISTA — {date.today()}")
    print("descrive e contestualizza; non prevede e non raccomanda. Mai.")

    spx = load_asset_monthly("SPX")

    # ---- 1. il tuo piano ---------------------------------------------------
    sezione("1 · IL TUO PIANO")
    if LEDGER.exists():
        ledger = pd.read_csv(LEDGER)
        prezzi = {s: load_asset_monthly(PROXY[s]) for s in ledger["strumento"].unique()
                  if s in PROXY}
        posizioni = valuta_ledger(ledger, prezzi)
        tot_v = sum(p.eur_versati for p in posizioni)
        tot_c = sum(p.valore for p in posizioni)
        for p in posizioni:
            print(f"  {p.strumento:6s} versati {p.eur_versati:>8.0f} € | "
                  f"valore ~{p.valore:>8.0f} € | {p.utile:+.0f} €  (proxy indice)")
        print(f"  {'TOTALE':6s} versati {tot_v:>8.0f} € | valore ~{tot_c:>8.0f} € | "
              f"{tot_c-tot_v:+.0f} €")
        n_mesi = ledger["data"].nunique()
        print(f"  versamenti registrati: {n_mesi} | la valutazione esatta è quella del broker")
    else:
        print(f"""  Piano firmato il 2026-07-18 ({IPS}); primo versamento non ancora
  registrato. Quando parte, si annota in {LEDGER.relative_to(Path.cwd()) if LEDGER.is_relative_to(Path.cwd()) else LEDGER}:
      data,strumento,eur,quote
      2026-08-01,ETF,300,0.061
  L'aderenza — non il rendimento — è la metrica del piano.""")

    # ---- 2. stato del mercato ---------------------------------------------
    sezione("2 · STATO DEL MERCATO — S&P 500 in EUR (proxy del tuo indice)")
    st = stato_corrente(spx)
    fase, nota = classifica(st.drawdown)
    ctx = contesto_storico(spx, st)
    print(f"  livello vs massimo storico ({st.mese_massimo}): {st.drawdown:+.1%}"
          + (f", da {st.mesi_dal_massimo} mesi" if st.mesi_dal_massimo else " — SUI MASSIMI"))
    print(f"  fase descrittiva: {fase.upper()} — {nota}")
    if st.drawdown < -0.05:
        rec = (f"mediana di recupero {ctx.mediana_mesi_recupero:.0f} mesi"
               if ctx.mediana_mesi_recupero else "recuperi storici in corso di calcolo")
        print(f"  contesto: in {ctx.anni_di_storia:.0f} anni, cali almeno così profondi: "
              f"{ctx.episodi_almeno_cosi} (mediana {ctx.mediana_profondita:.0%}, {rec})")
    else:
        print(f"  contesto: {ctx.anni_di_storia:.0f} anni di storia alle spalle; "
              "stare sui massimi è lo stato più frequente di un indice che cresce")

    # ---- 3. i numeri del mese ---------------------------------------------
    sezione("3 · I NUMERI DEL PERIODO")
    print(f"  ultimo mese: {st.ret_1m:+.1%}  (più mosso del "
          f"{ctx.percentile_ret_1m:.0%} dei mesi storici)")
    print(f"  ultimi 3 mesi: {st.ret_3m:+.1%} | ultimi 12 mesi: {st.ret_12m:+.1%}")
    print("  (numeri descrittivi: nessuno di questi contiene informazione sul mese prossimo)")

    # ---- 4bis. semaforo del carry (descrittivo, fase 1 studio medio termine)
    sezione("SEMAFORO DEL CARRY — strategia promossa, dormiente (descrittivo)")
    from src.research.carry_monitor import (STORICO_CARRY, basis_corrente,
                                            fascia_regime, funding_corrente,
                                            percentile_storico)
    fc = funding_corrente()
    if fc:
        fascia, nota = fascia_regime(fc["mediana"])
        pct = percentile_storico(fc["mediana"])
        print(f"  funding mediano 30gg (annualizzato): {fc['mediana']:+.1%} "
              f"| positivo su {fc['positivi']}/{fc['totale']} simboli")
        print(f"  regime: {fascia} — {nota}")
        if pct is not None:
            print(f"  percentile storico (2020-2026): {pct:.0%}")
    else:
        print("  funding live non disponibile (API irraggiungibile)")
    bc = basis_corrente()
    if bc:
        for sott, b in bc.items():
            print(f"  basis {sott} trimestrale ({b['simbolo']}, {b['giorni']:.0f}gg): "
                  f"{b['basis_annuo']:+.1%} annuo")
    print("  riferimento (carry_v1, netto/anno): " +
          " | ".join(f"{k} {v:+.1%}" for k, v in STORICO_CARRY.items()))
    stato_paper = Path(__file__).parent.parent / "data" / "carry_paper" / "state.json"
    if stato_paper.exists():
        import json
        sp = json.loads(stato_paper.read_text())
        aperto = sum(p["funding_incassato"] for p in sp["posizioni"].values())
        print(f"  paper executor: {len(sp['posizioni'])} posizioni | funding "
              f"incassato {sp['funding_totale']:+.2f} | PnL realizzato "
              f"{sp['pnl_realizzato']:+.2f} | costi {sp['costi_pagati']:.2f} "
              f"(USDT di carta, da {sp['avvio'][:10]})")
    print("  NB: pannello descrittivo. L'eventuale riattivazione del carry "
          "richiede un pre-registro proprio (docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md).")

    # ---- 4. eco dell'IPS ---------------------------------------------------
    sezione("4 · ECO DEL TUO IPS")
    if st.drawdown <= -0.15:
        print(f"""  Il mercato è in {fase}: è IL momento per cui hai scritto il protocollo.
  Dal tuo documento ({IPS}):
    1. rileggi l'attestazione del test dello stomaco
    2. il controllo del portafoglio resta al massimo mensile
    3. il bonifico parte regolare (ammesso raddoppiarlo, mai ridurlo)
    4. nessuna vendita, nessun cambio di allocazione (30 giorni di attesa)
  Firmato da te a mente fredda, per leggerlo adesso.""")
    else:
        print("""  Nessuna azione richiesta dal mercato — come quasi sempre. L'unica cosa
  che il piano prevede è il prossimo bonifico, alla data stabilita. Se hai
  voglia di 'fare qualcosa': è il segnale di rileggere la sezione satellite
  dell'IPS, non di aprire il broker.""")
    print()


if __name__ == "__main__":
    main()
