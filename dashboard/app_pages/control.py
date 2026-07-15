import os

import streamlit as st

from src.core.models import Config
from utils import formatting, process_manager
from utils.redis_client import get_heartbeat, get_trading_config, publish_engine_command, save_trading_config

SERVICES = ["engine", "inference", "sentiment"]
STALE_AFTER_SECONDS = {"engine": 20, "inference": 20, "sentiment": 60}
STATUS_ICON = {"ok": "🟢", "stale": "🟡", "down": "🔴"}

# Nello stack Docker ogni servizio è un container: il process_manager
# (start.sh + PID locali) non può avviarli/fermarli da dentro il container
# della dashboard, quindi mostriamo solo lo stato dagli heartbeat.
IN_DOCKER = bool(os.getenv("HERMES_IN_DOCKER"))

st.subheader("Processi")
if IN_DOCKER:
    st.info(
        "Stack gestito da Docker Compose: per avviare/fermare i servizi usa "
        "`docker compose start|stop|restart engine|inference|sentiment`. "
        "Lo stato qui sotto riflette gli heartbeat su Redis."
    )
    for service in SERVICES:
        heartbeat = get_heartbeat(service)
        # Heartbeat assente = servizio giù; presente ma vecchio = stale.
        if not heartbeat:
            state = "down"
        elif formatting.age_seconds(heartbeat) <= STALE_AFTER_SECONDS[service]:
            state = "ok"
        else:
            state = "stale"
        with st.container(border=True):
            st.markdown(f"**{STATUS_ICON[state]} {service.capitalize()}** — container `{service}`")

for service in ([] if IN_DOCKER else SERVICES):
    proc_status = process_manager.status(service)
    heartbeat = get_heartbeat(service)
    state = formatting.heartbeat_status(heartbeat, proc_status["running"], STALE_AFTER_SECONDS[service])

    with st.container(border=True):
        st.markdown(f"**{STATUS_ICON[state]} {service.capitalize()}** — PID: {proc_status['pids'] or '—'}")

        with st.container(horizontal=True):
            if st.button("Avvia", key=f"start_{service}", disabled=proc_status["running"]):
                ok, msg = process_manager.start(service)
                (st.success if ok else st.warning)(msg)
                st.rerun()

            stop_confirm_key = f"confirm_stop_{service}"
            if not st.session_state.get(stop_confirm_key, False):
                if st.button("Ferma", key=f"stop_{service}", disabled=not proc_status["running"]):
                    st.session_state[stop_confirm_key] = True
                    st.rerun()

        if st.session_state.get(stop_confirm_key, False):
            st.warning(f"Confermi l'arresto di {service}? Il processo verrà terminato.")
            with st.container(horizontal=True):
                if st.button("Conferma arresto", key=f"confirm_stop_yes_{service}"):
                    ok, msg = process_manager.stop(service)
                    st.session_state[stop_confirm_key] = False
                    (st.success if ok else st.warning)(msg)
                    st.rerun()
                if st.button("Annulla", key=f"confirm_stop_no_{service}"):
                    st.session_state[stop_confirm_key] = False
                    st.rerun()

st.subheader("Funzionalità")
current_config = get_trading_config() or {}
config_obj = Config(**current_config) if current_config else Config()

with st.container(horizontal=True):
    reverse = st.toggle(
        "Reverse trading", value=config_obj.reverse_trading_enabled, key="toggle_reverse"
    )
    pattern = st.toggle(
        "Pattern confirmation", value=config_obj.pattern_confirmation_enabled, key="toggle_pattern"
    )
    dynamic_exit = st.toggle(
        "Dynamic exit", value=config_obj.dynamic_exit_enabled, key="toggle_dynamic_exit"
    )

if (
    reverse != config_obj.reverse_trading_enabled
    or pattern != config_obj.pattern_confirmation_enabled
    or dynamic_exit != config_obj.dynamic_exit_enabled
):
    updated = config_obj.model_copy(
        update={
            "reverse_trading_enabled": reverse,
            "pattern_confirmation_enabled": pattern,
            "dynamic_exit_enabled": dynamic_exit,
        }
    )
    save_trading_config(updated.model_dump())
    st.toast("Configurazione aggiornata e pubblicata")
    st.rerun()

st.subheader("Azioni di emergenza")
reset_confirm_key = "confirm_reset_positions"
if not st.session_state.get(reset_confirm_key, False):
    if st.button("Reset posizioni (emergenza)", type="primary"):
        st.session_state[reset_confirm_key] = True
        st.rerun()
else:
    st.error(
        "Questo chiuderà TUTTE le posizioni aperte tramite il normale flusso di chiusura "
        "dell'engine (storico e notifiche coerenti). Confermi?"
    )
    with st.container(horizontal=True):
        if st.button("Conferma chiusura di tutte le posizioni", key="confirm_reset_yes"):
            publish_engine_command("close_all", reason="EMERGENCY_RESET")
            st.session_state[reset_confirm_key] = False
            st.success("Comando di chiusura inviato all'engine.")
            st.rerun()
        if st.button("Annulla", key="confirm_reset_no"):
            st.session_state[reset_confirm_key] = False
            st.rerun()
