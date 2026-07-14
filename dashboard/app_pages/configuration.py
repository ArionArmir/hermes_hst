import yaml
import streamlit as st
from pydantic import ValidationError

from src.core.models import Config
from utils.redis_client import get_trading_config, save_trading_config

CONFIG_YAML_PATH = "config/trading_params.yaml"


def _load_current_config() -> dict:
    config_dict = get_trading_config()
    if config_dict:
        return config_dict
    try:
        with open(CONFIG_YAML_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


current = _load_current_config()
defaults = Config(**current) if current else Config()

with st.form("trading_config_form"):
    st.subheader("Parametri di trading")
    col1, col2 = st.columns(2)
    with col1:
        leverage = st.number_input("Leva", min_value=1, max_value=20, value=defaults.leverage, step=1)
        stop_loss_pct = st.number_input(
            "Stop loss %", min_value=0.0, max_value=1.0, value=defaults.stop_loss_pct, format="%.4f"
        )
        take_profit_pct = st.number_input(
            "Take profit %", min_value=0.0, max_value=1.0, value=defaults.take_profit_pct, format="%.4f"
        )
        max_position_size_usdt = st.number_input(
            "Dimensione massima posizione (USDT)", min_value=0.0, value=defaults.max_position_size_usdt
        )
        trailing_stop_pct = st.number_input(
            "Trailing stop %", min_value=0.0, max_value=1.0, value=defaults.trailing_stop_pct, format="%.4f"
        )
        max_exposure = st.number_input(
            "Esposizione massima", min_value=0.0, max_value=1.0, value=defaults.max_exposure, format="%.2f"
        )
    with col2:
        min_volatility_threshold = st.number_input(
            "Soglia volatilità minima", min_value=0.0, value=defaults.min_volatility_threshold, format="%.4f"
        )
        max_volatility_threshold = st.number_input(
            "Soglia volatilità massima", min_value=0.0, value=defaults.max_volatility_threshold, format="%.4f"
        )
        volatility_adjustment = st.checkbox(
            "Aggiustamento volatilità", value=defaults.volatility_adjustment
        )
        ml_confidence_threshold = st.number_input(
            "Soglia confidenza ML", min_value=0.0, max_value=1.0, value=defaults.ml_confidence_threshold, format="%.2f"
        )
        sentiment_weight = st.number_input(
            "Peso sentiment", min_value=0.0, max_value=1.0, value=defaults.sentiment_weight, format="%.2f"
        )
        sentiment_asset_enabled = st.checkbox(
            "Sentiment per asset attivo", value=defaults.sentiment_asset_enabled
        )

    symbols_text = st.text_input("Simboli (separati da virgola)", value=", ".join(defaults.symbols))
    timeframe = st.text_input("Timeframe", value=defaults.timeframe)

    st.subheader("Funzionalità")
    col3, col4, col5 = st.columns(3)
    with col3:
        reverse_trading_enabled = st.checkbox("Reverse trading", value=defaults.reverse_trading_enabled)
    with col4:
        pattern_confirmation_enabled = st.checkbox(
            "Pattern confirmation", value=defaults.pattern_confirmation_enabled
        )
    with col5:
        dynamic_exit_enabled = st.checkbox("Dynamic exit", value=defaults.dynamic_exit_enabled)

    submitted = st.form_submit_button("Salva e applica")

if submitted:
    new_config_dict = dict(
        leverage=int(leverage),
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_position_size_usdt=max_position_size_usdt,
        trailing_stop_pct=trailing_stop_pct,
        max_exposure=max_exposure,
        min_volatility_threshold=min_volatility_threshold,
        max_volatility_threshold=max_volatility_threshold,
        volatility_adjustment=volatility_adjustment,
        symbols=[s.strip().upper() for s in symbols_text.split(",") if s.strip()],
        timeframe=timeframe,
        ml_confidence_threshold=ml_confidence_threshold,
        sentiment_weight=sentiment_weight,
        sentiment_asset_enabled=sentiment_asset_enabled,
        reverse_trading_enabled=reverse_trading_enabled,
        pattern_confirmation_enabled=pattern_confirmation_enabled,
        dynamic_exit_enabled=dynamic_exit_enabled,
    )
    try:
        validated = Config(**new_config_dict)
    except ValidationError as e:
        st.error(f"Configurazione non valida: {e}")
    else:
        save_trading_config(validated.model_dump())
        try:
            with open(CONFIG_YAML_PATH, "w") as f:
                yaml.safe_dump(validated.model_dump(), f, sort_keys=False)
        except OSError as e:
            st.warning(f"Salvato su Redis, ma non su YAML: {e}")
        st.success("Configurazione salvata e pubblicata. L'engine la ricaricherà automaticamente al prossimo config_updated.")
        st.rerun()
