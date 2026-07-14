from pathlib import Path

import streamlit as st

from utils.process_manager import LOG_PREFIX, REPO_ROOT

SERVICES = ["engine", "inference", "sentiment"]
LEVELS = ["INFO", "WARNING", "ERROR", "DEBUG"]
LOG_DIR = REPO_ROOT / "logs"


def _read_tail(path: Path, levels_filter, n: int = 500) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    lines = lines[-n:]
    if levels_filter:
        lines = [line for line in lines if any(f" {lvl} " in line for lvl in levels_filter)]
    return "".join(lines[-300:])


with st.container(horizontal=True):
    service = st.segmented_control("Servizio", SERVICES, default=SERVICES[0], format_func=str.capitalize)
    levels = st.pills("Livello", LEVELS, selection_mode="multi")

service = service or SERVICES[0]
prefix = LOG_PREFIX[service]
candidates = sorted(LOG_DIR.glob(f"{prefix}_*.log"))

if not candidates:
    st.info("Nessun file di log trovato per questo servizio")
else:
    labels = [p.name for p in candidates]
    selected_label = st.selectbox("File", labels, index=len(labels) - 1)
    selected_path = LOG_DIR / selected_label
    is_latest_file = selected_label == labels[-1]

    if is_latest_file:
        @st.fragment(run_every="2s")
        def render_tail():
            st.code(_read_tail(selected_path, levels), language="log")

        render_tail()
    else:
        st.code(_read_tail(selected_path, levels), language="log")

    st.download_button(
        "Scarica il file completo",
        data=selected_path.read_bytes(),
        file_name=selected_label,
        mime="text/plain",
    )
