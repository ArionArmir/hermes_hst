"""
Pagina Piano: il PAC dell'IPS (300 EUR/mese) simulato con dati reali sui due
fondi consigliati — S&P 500 e FTSE All-World (VWCE) — e, quando il ledger
esisterà, il piano vero contro la simulazione.

Come tutto il lato investimento: descrive, non prevede. La colonna "utile"
è il passato, non una promessa.
"""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.invest.drawdown import drawdown_episodes, load_asset_monthly, simulate_dca

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data" / "invest" / "ledger.csv"
MENSILE = 300.0

FONDI = {
    "VWCE (All-World, acc.)": ("VWCE",
                               "in EUR, dividendi reinvestiti nel prezzo — lo strumento del piano"),
    "S&P 500 (indice prezzo)": ("SPX",
                                "in EUR via cambio; SENZA dividendi: sottostima ~1.5-2%/anno"),
}


@st.cache_data(ttl="6h", show_spinner="Carico le serie dei fondi…")
def _serie(nome: str) -> pd.Series:
    return load_asset_monthly(nome)


st.caption("Il piano firmato (docs/PRE_REGISTRO_INVESTIMENTO.md): 300 €/mese, "
           "100% azionario indicizzato, vendite solo per bisogni di vita. "
           "Questa pagina lo simula con dati reali — non lo modifica.")

# ---- il piano vero, se avviato -------------------------------------------
st.subheader("Il piano reale")
if LEDGER.exists():
    ledger = pd.read_csv(LEDGER)
    st.dataframe(ledger, hide_index=True)
else:
    st.info("Primo versamento non ancora registrato. Quando parte: una riga "
            "in `data/invest/ledger.csv` (data,strumento,eur,quote) e questa "
            "sezione mostrerà il piano vero accanto alla simulazione.")

# ---- simulazione ----------------------------------------------------------
st.subheader("Simulazione: 300 €/mese, dati reali")
vwce = _serie("VWCE")
spx = _serie("SPX")
serie = {"VWCE (All-World, acc.)": vwce, "S&P 500 (indice prezzo)": spx}

inizio_min = vwce.index[0]                       # VWCE esiste dal 2019-06
anni = sorted({str(p.year) for p in vwce.index})
col_a, _ = st.columns([1, 3])
with col_a:
    da_anno = st.selectbox("Partenza della simulazione", anni, index=0,
                           help="Il DCA parte da gennaio dell'anno scelto "
                                "(o dal primo mese disponibile)")

righe_kpi, curve = [], []
for etichetta, s in serie.items():
    fin = s[s.index >= max(inizio_min, pd.Period(f"{da_anno}-01", freq="M"))]
    out = simulate_dca(fin.to_frame(), {s.name: 1.0}, MENSILE)
    eps = drawdown_episodes(out.unit_value, minimo=0.10)
    righe_kpi.append({
        "fondo": etichetta,
        "versato": out.versato.iloc[-1],
        "valore": out.conto.iloc[-1],
        "utile": out.conto.iloc[-1] - out.versato.iloc[-1],
        "dd": eps[0].profondita if eps else 0.0,
    })
    df = pd.DataFrame({"mese": out.conto.index.to_timestamp(),
                       "valore": out.conto.values,
                       "versato": out.versato.values, "fondo": etichetta})
    curve.append(df)

for r in righe_kpi:
    with st.container(horizontal=True):
        st.metric(r["fondo"], f"{r['valore']:,.0f} €", border=True)
        st.metric("Versato", f"{r['versato']:,.0f} €", border=True)
        st.metric("Risultato", f"{r['utile']:+,.0f} €",
                  f"{r['utile']/r['versato']:+.1%}", border=True)
        st.metric("Drawdown peggiore attraversato", f"{r['dd']:.0%}", border=True)

tutte = pd.concat(curve, ignore_index=True)
versato_ref = curve[0][["mese", "versato"]]
grafico = (alt.Chart(tutte).mark_line()
           .encode(x=alt.X("mese:T", title=None),
                   y=alt.Y("valore:Q", title="EUR"),
                   color=alt.Color("fondo:N", title=None),
                   tooltip=["mese:T", "fondo", alt.Tooltip("valore:Q", format=",.0f")]))
linea_versato = (alt.Chart(versato_ref).mark_line(strokeDash=[4, 4], color="gray")
                 .encode(x="mese:T", y="versato:Q"))
st.altair_chart(grafico + linea_versato, width="stretch")
st.caption("Linea grigia tratteggiata: il totale versato. La distanza tra le "
           "curve e la linea è l'utile — e nei crolli le curve le passeranno "
           "sotto: è previsto dal piano, non un suo fallimento.")

# ---- il promemoria che conta ----------------------------------------------
with st.container(border=True):
    st.markdown(
        "**Promemoria dell'IPS** — la colonna 'utile' è il passato, non una "
        "promessa; l'aspettativa dichiarata resta ~6-8%/anno su decenni, con "
        "anni a −20/−50% dentro. Il listino vero dell'azionario: 2008 −53%, "
        "65 mesi sotto il picco. Se guardando queste curve viene voglia di "
        "'fare qualcosa': il piano prevede una sola azione, il prossimo "
        "bonifico.")
