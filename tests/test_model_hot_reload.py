"""
Hot-reload del modello (docs/IMPROVEMENT_PLAN.md, M4): il trainer pubblica
su 'model_swap' dopo ogni promozione, l'inference deve ricaricare il champion
senza riavvio. 'config_updated' continua a ricaricare solo la config.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inference.main import MLInference


def test_model_swap_message_triggers_model_reload():
    inference = MLInference()
    with patch.object(inference, "_load_model") as load_model, \
         patch.object(inference, "_load_config_from_redis", new_callable=AsyncMock) as load_config:
        asyncio.run(inference._on_pubsub_message("model_swap"))

    load_model.assert_called_once()
    load_config.assert_not_called()


def test_config_updated_message_triggers_config_reload_only():
    inference = MLInference()
    with patch.object(inference, "_load_model") as load_model, \
         patch.object(inference, "_load_config_from_redis", new_callable=AsyncMock) as load_config:
        asyncio.run(inference._on_pubsub_message("config_updated"))

    load_config.assert_awaited_once()
    load_model.assert_not_called()


def test_bytes_channel_is_decoded():
    inference = MLInference()
    with patch.object(inference, "_load_model") as load_model:
        asyncio.run(inference._on_pubsub_message(b"model_swap"))

    load_model.assert_called_once()
