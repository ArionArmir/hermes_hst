"""Tripwire del carry — la memoria del semaforo tra un mese e l'altro.

Il protocollo (docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md) scatta con fascia
RICCA per due rapporti mensili consecutivi. Questo modulo tiene la storia
delle letture e decide lo scatto: logica pura + un file di stato.

Lo scatto NON attiva nulla: fa urlare il rapporto e lascia un marker. Ogni
passo successivo è umano, per costruzione.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
STATE = _ROOT / "data" / "invest" / "tripwire.json"
MARKER = _ROOT / "data" / "invest" / "TRIPWIRE_SCATTATO"

FASCIA_CHE_CONTA = "RICCO"
CONSECUTIVI_RICHIESTI = 2


def _mesi_adiacenti(mesi: list[str]) -> bool:
    """True se i mesi 'YYYY-MM' sono consecutivi senza salti. Senza questa
    verifica (revisione branch 2026-07-21) gen+mar con febbraio SALTATO
    scattava come 'due consecutivi', contro il docstring: un mese ignoto
    (pipeline giù) non è una conferma."""
    indici = [int(m[:4]) * 12 + int(m[5:7]) for m in mesi]
    return all(b - a == 1 for a, b in zip(indici, indici[1:]))


def aggiorna(stato: dict, mese: str, fascia: str, mediana: float) -> tuple[dict, bool]:
    """Registra la lettura del mese (idempotente: ripetere lo stesso mese
    sovrascrive) e dice se il tripwire scatta ORA."""
    storia = [r for r in stato.get("storia", []) if r["mese"] != mese]
    storia.append({"mese": mese, "fascia": fascia, "mediana": round(mediana, 4)})
    storia.sort(key=lambda r: r["mese"])
    stato["storia"] = storia

    ultimi = storia[-CONSECUTIVI_RICHIESTI:]
    scattato = (len(ultimi) == CONSECUTIVI_RICHIESTI
                and all(r["fascia"] == FASCIA_CHE_CONTA for r in ultimi)
                and _mesi_adiacenti([r["mese"] for r in ultimi]))
    gia_scattato = stato.get("scattato", False)
    stato["scattato"] = scattato or gia_scattato
    return stato, scattato and not gia_scattato


def consecutivi_correnti(stato: dict) -> int:
    """Quante letture RICCO consecutive chiudono la storia (0 se l'ultima
    non lo è)."""
    n = 0
    for r in reversed(stato.get("storia", [])):
        if r["fascia"] != FASCIA_CHE_CONTA:
            break
        n += 1
    return n


def carica() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"storia": [], "scattato": False}


def salva(stato: dict, scattato_ora: bool = False) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(stato, indent=1))
    if scattato_ora:
        MARKER.write_text("Fascia RICCA per 2 rapporti consecutivi. "
                          "Prossimo passo: scrivere il pre-registro di attivazione. "
                          "Vedi docs/PROTOCOLLO_RIATTIVAZIONE_CARRY.md\n")
