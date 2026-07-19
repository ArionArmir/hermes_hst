"""
Entry point della dashboard Hermes HFT. Guscio sottile di navigazione: il contenuto
di ogni pagina vive in app_pages/. streamlit run dashboard/app.py (solo localhost,
nessun --server.address 0.0.0.0: la dashboard può modificare config e posizioni).
"""
import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DASHBOARD_DIR.parent
for _path in (_DASHBOARD_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pandas as pd
import streamlit as st

# Difesa in profondità contro i segfault pyarrow visti al cambio tab/pagina:
# la causa vera era pyarrow 25 (ora pinnato a 22 in requirements.txt), ma le
# stringhe arrow-backed di pandas 3 erano uno dei punti di crash e per i
# piccoli DataFrame della dashboard lo storage "python" è identico. Vale solo
# per questo processo; engine/inference non sono toccati.
pd.set_option("mode.string_storage", "python")

st.set_page_config(page_title="Hermes Dashboard", layout="wide", page_icon=":material/monitoring:")

pages = st.navigation([
    st.Page("app_pages/home.py", title="Dashboard", icon=":material/monitoring:", default=True),
    st.Page("app_pages/analysis.py", title="Analisi", icon=":material/analytics:"),
    st.Page("app_pages/carry.py", title="Carry", icon=":material/savings:"),
    st.Page("app_pages/forward.py", title="Forward 0.50", icon=":material/science:"),
    st.Page("app_pages/configuration.py", title="Configurazione", icon=":material/settings:"),
    st.Page("app_pages/control.py", title="Controllo", icon=":material/tune:"),
    st.Page("app_pages/logs.py", title="Log", icon=":material/description:"),
])

st.title(f"{pages.icon} {pages.title}")
pages.run()
