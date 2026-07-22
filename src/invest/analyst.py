"""L'analista che non prevede — motore del contesto di mercato.

Descrive ciò che sta succedendo confrontando il presente con la distribuzione
del passato: mai una previsione, mai un compra/vendi. Il valore economico di
questo mestiere è il behavior gap (~1,5-2%/anno): l'analista che ti tiene
seduto, non quello che indovina.

Funzioni pure su serie mensili: la parte testabile del rapporto.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.invest.drawdown import Episodio, drawdown_episodes


@dataclass
class StatoMercato:
    livello: float
    massimo: float
    mese_massimo: pd.Period
    drawdown: float               # es. -0.08
    mesi_dal_massimo: int
    ret_1m: float
    ret_3m: float
    ret_12m: float


def stato_corrente(serie: pd.Series) -> StatoMercato:
    picco, quando = serie.max(), serie.idxmax()
    oggi = serie.iloc[-1]
    return StatoMercato(
        livello=float(oggi), massimo=float(picco), mese_massimo=quando,
        drawdown=float(oggi / picco - 1),
        mesi_dal_massimo=(serie.index[-1] - quando).n,
        ret_1m=float(serie.iloc[-1] / serie.iloc[-2] - 1) if len(serie) > 1 else 0.0,
        ret_3m=float(serie.iloc[-1] / serie.iloc[-4] - 1) if len(serie) > 3 else 0.0,
        ret_12m=float(serie.iloc[-1] / serie.iloc[-13] - 1) if len(serie) > 12 else 0.0,
    )


# Le soglie sono convenzioni descrittive del mestiere, non segnali:
# classificano il presente, non raccomandano nulla.
FASI = [
    (-0.10, "ordinaria amministrazione", "oscillazione normale: nel gioco 2 non è un evento"),
    (-0.20, "correzione", "frequente e storicamente sempre recuperata: il piano non la vede"),
    (-0.40, "mercato orso", "l'evento per cui è stato scritto il protocollo del crollo"),
    (-1.00, "episodio di coda", "raro (2000, 2008): l'attestazione dell'IPS parla di questo"),
]


def classifica(drawdown: float) -> tuple[str, str]:
    for soglia, nome, nota in FASI:
        if drawdown > soglia:
            return nome, nota
    return FASI[-1][1], FASI[-1][2]


@dataclass
class Contesto:
    episodi_almeno_cosi: int      # quanti episodi storici hanno raggiunto questa profondità
    anni_di_storia: float
    mediana_profondita: float     # degli episodi comparabili
    mediana_mesi_recupero: float | None
    percentile_ret_1m: float      # il rendimento dell'ultimo mese vs la storia


def contesto_storico(serie: pd.Series, stato: StatoMercato) -> Contesto:
    """Quante volte la storia ha visto (almeno) il presente, e com'è finita."""
    episodi = drawdown_episodes(serie, minimo=max(0.05, abs(stato.drawdown)))
    # l'episodio ancora aperto È il presente: "quante volte la storia ha visto
    # il presente" non deve contarlo (solo l'ultimo può avere recupero None)
    comparabili = [e for e in episodi
                   if e.recupero is not None and e.profondita <= min(-0.05, stato.drawdown)]
    recuperi = [e.mesi_sotto for e in comparabili if e.recupero is not None]
    rendimenti = serie.pct_change().dropna()
    return Contesto(
        episodi_almeno_cosi=len(comparabili),
        anni_di_storia=(serie.index[-1] - serie.index[0]).n / 12,
        mediana_profondita=(pd.Series([e.profondita for e in comparabili]).median()
                            if comparabili else 0.0),
        mediana_mesi_recupero=(pd.Series(recuperi).median() if recuperi else None),
        percentile_ret_1m=float((rendimenti < stato.ret_1m).mean()),
    )


@dataclass
class Posizione:
    strumento: str
    eur_versati: float
    quote: float
    valore: float

    @property
    def utile(self) -> float:
        return self.valore - self.eur_versati


def valuta_ledger(ledger: pd.DataFrame, prezzi: dict[str, pd.Series]) -> list[Posizione]:
    """Valorizza gli acquisti registrati ai prezzi correnti (proxy indice:
    la valutazione esatta è quella del broker, e il rapporto lo dice)."""
    out = []
    for strumento, g in ledger.groupby("strumento"):
        serie = prezzi.get(strumento)
        if serie is None:
            continue
        quote = float(g["quote"].sum())
        out.append(Posizione(strumento=str(strumento),
                             eur_versati=float(g["eur"].sum()),
                             quote=quote,
                             valore=quote * float(serie.iloc[-1])))
    return out
