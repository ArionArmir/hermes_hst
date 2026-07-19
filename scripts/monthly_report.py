"""Il rapporto mensile automatico — il battito lento dell'ecosistema.

Lanciato dal timer systemd il primo del mese (Persistent=true: se la macchina
era spenta, recupera al riavvio). Fa quattro cose:
1. genera il rapporto dell'analista e lo salva in docs/rapporti/AAAA-MM.md
2. aggiorna il tripwire del carry con la fascia del mese
3. se il tripwire scatta: lo urla in testa al rapporto e lascia il marker
4. aggiunge il calendario degli esperimenti (a che punto sono i verdetti)

Principio: tempo reale per la macchina, cadenza mensile per l'umano.

Uso:  venv/bin/python scripts/monthly_report.py
"""
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
RAPPORTI = ROOT / "docs" / "rapporti"


def calendario_esperimenti() -> list[str]:
    righe = ["", "=" * 78, "CALENDARIO DEGLI ESPERIMENTI", "=" * 78]
    # forward_v1: trade accumulati verso i 100 (o lettura al 2027-01-19)
    try:
        from src.shared import store
        segnali = store.read_signals(limit=100_000)
        aperti = int((segnali["outcome"] == "OPENED").sum()) if len(segnali) else 0
        righe.append(f"  forward_v1 (soglia 0.50): {aperti}/100 trade "
                     f"| lettura entro il 2027-01-19")
    except Exception as e:
        righe.append(f"  forward_v1: stato non leggibile ({e})")
    # carry_paper_v1: ribilanciamenti verso i 50 (o 26 settimane)
    try:
        s = json.loads((ROOT / "data" / "carry_paper" / "state.json").read_text())
        avvio = datetime.fromisoformat(s["avvio"])
        settimane = (datetime.now(timezone.utc) - avvio).days / 7
        righe.append(f"  carry_paper_v1: {s['ribilanciamenti']}/50 ribilanciamenti, "
                     f"settimana {settimane:.0f}/26 | funding incassato "
                     f"{s['funding_totale']:+.2f} | PnL realizzato "
                     f"{s['pnl_realizzato']:+.2f} (USDT carta)")
    except Exception:
        righe.append("  carry_paper_v1: stato non disponibile")
    return righe


def main():
    mese = date.today().strftime("%Y-%m")
    RAPPORTI.mkdir(parents=True, exist_ok=True)

    # 1) il rapporto dell'analista, catturato
    buf = io.StringIO()
    sys.argv = ["analyst_report"]
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "analyst_report", ROOT / "scripts" / "analyst_report.py")
    mod = importlib.util.module_from_spec(spec)
    with redirect_stdout(buf):
        spec.loader.exec_module(mod)
        mod.main()
    corpo = buf.getvalue()

    # 2) tripwire: fascia del mese dal semaforo
    from src.invest.tripwire import aggiorna, carica, consecutivi_correnti, salva
    from src.research.carry_monitor import fascia_regime, funding_corrente
    testa = []
    fc = funding_corrente()
    if fc:
        fascia, _ = fascia_regime(fc["mediana"])
        stato, scattato_ora = aggiorna(carica(), mese, fascia, fc["mediana"])
        salva(stato, scattato_ora)
        if scattato_ora or stato.get("scattato"):
            testa = ["#" * 78,
                     "##  TRIPWIRE DEL CARRY SCATTATO: fascia RICCA per 2 mesi consecutivi",
                     "##  Prossimo passo (umano): scrivere il pre-registro di attivazione.",
                     "##  Vedi docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md",
                     "#" * 78, ""]
        else:
            testa = [f"[tripwire carry: {consecutivi_correnti(stato)}/2 letture "
                     f"RICCO consecutive — fascia del mese: {fascia}]", ""]

    # 3+4) composizione e salvataggio
    out = RAPPORTI / f"{mese}.md"
    out.write_text("```\n" + "\n".join(testa) + corpo
                   + "\n".join(calendario_esperimenti()) + "\n```\n")
    print(f"rapporto salvato: {out}")


if __name__ == "__main__":
    main()
