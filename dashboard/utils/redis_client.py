"""
Client Redis sincrono per la dashboard (redis-py, non l'async src/shared/redis_client.py).
Evita di dover gestire un event loop dentro Streamlit.
"""
import json
import os
from typing import Optional

import redis
import streamlit as st

HEARTBEAT_SERVICES = ("engine", "inference", "sentiment", "carry", "liquidations")


@st.cache_resource
def get_client() -> redis.Redis:
    return redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                       port=int(os.getenv("REDIS_PORT", "6379")),
                       decode_responses=True)


def get_json(key: str) -> Optional[dict]:
    raw = get_client().get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_positions() -> dict:
    return get_json("positions") or {}


def get_latest_price(symbol: str) -> Optional[float]:
    raw = get_client().get(f"latest_price_{symbol}")
    return float(raw) if raw else None


def get_heartbeat(service: str) -> Optional[str]:
    return get_client().get(f"heartbeat_{service}")


def get_last_tick(service: str) -> Optional[str]:
    """Timestamp dell'ultimo tick WebSocket realmente elaborato da engine/inference
    (distinto dall'heartbeat: un processo può essere vivo e loggare regolarmente
    pur avendo una connessione WebSocket "zombie" che non consegna più dati)."""
    return get_client().get(f"last_tick_{service}")


def get_trading_config() -> Optional[dict]:
    return get_json("trading_config")


def get_circuit_breaker_status() -> Optional[dict]:
    """Pubblicato dall'engine ogni 5s (_position_monitor). None se
    l'engine non è mai partito col codice che lo pubblica."""
    return get_json("circuit_breaker_status")


def save_trading_config(config_dict: dict):
    client = get_client()
    client.set("trading_config", json.dumps(config_dict))
    client.publish("config_updated", "1")


def publish_engine_command(action: str, reason: str = ""):
    payload = {"action": action}
    if reason:
        payload["reason"] = reason
    get_client().publish("engine_commands", json.dumps(payload))


def get_sentiment_score() -> Optional[float]:
    raw = get_client().get("sentiment_score")
    return float(raw) if raw else None


def get_sentiment_by_asset() -> dict:
    """sentiment_asset è solo pubblicato via pubsub: gli unici valori persistiti
    sono le chiavi sentiment_{asset} scritte da src/sentiment/ollama_client.py
    per tutti gli asset configurati. Un asset senza chiave (es. appena
    aggiunto, prima del prossimo ciclo sentiment) semplicemente non compare
    nel risultato, nessun errore."""
    client = get_client()
    config = get_trading_config()
    symbols = (config or {}).get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assets = [s.upper().replace("USDT", "") for s in symbols]
    result = {}
    for asset in assets:
        raw = client.get(f"sentiment_{asset.lower()}")
        if raw is not None:
            result[asset] = float(raw)
    return result
