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

import streamlit as st

st.set_page_config(page_title="Hermes Dashboard", layout="wide", page_icon=":material/monitoring:")

pages = st.navigation([
    st.Page("app_pages/home.py", title="Dashboard", icon=":material/monitoring:", default=True),
    st.Page("app_pages/configuration.py", title="Configurazione", icon=":material/settings:"),
    st.Page("app_pages/control.py", title="Controllo", icon=":material/tune:"),
    st.Page("app_pages/logs.py", title="Log", icon=":material/description:"),
])

st.title(f"{pages.icon} {pages.title}")
pages.run()
