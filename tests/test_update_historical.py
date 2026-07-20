"""
Refresh incrementale dei parquet storici (DataCollector.update_historical):
il retraining schedulato deve vedere le candele nuove, l'ultima candela
salvata (potenzialmente parziale al momento del download) va sostituita
dalla versione definitiva, e un parquet assente scatena il download completo.
Fetch di rete mockato.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_collector import DataCollector, HISTORY_DAYS


def _candles(start: str, n: int, volume: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": volume,
         "taker_buy_base": volume * 0.5, "n_trades": 100.0},
        index=idx,
    ).rename_axis("timestamp")


def _make_collector():
    # Niente exchange reale: si testano solo merge e persistenza
    with patch.object(DataCollector, "__init__", lambda self: None):
        return DataCollector()


def test_extends_existing_parquet_with_new_candles(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    collector = _make_collector()
    collector.save_to_parquet(_candles("2026-07-01", 24), "BTCUSDT")

    # Come nel caso reale, il fetch riparte DALL'ultima candela salvata
    # (23:00 dell'1/7, inclusa) e porta 12 candele nuove
    new = _candles("2026-07-01 23:00", 13)
    with patch.object(collector, "fetch_ohlcv", side_effect=[new, pd.DataFrame()]):
        df = collector.update_historical("BTC/USDT", "BTCUSDT")

    # 24 esistenti + 13 scaricate − 1 sovrapposta = 36
    assert len(df) == 36
    assert df.index.max() == pd.Timestamp("2026-07-02 11:00")
    assert not df.index.duplicated().any()
    # E il parquet su disco è stato aggiornato
    assert len(collector.load_historical("BTCUSDT")) == 36


def test_overlapping_candle_is_replaced_by_final_version(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    collector = _make_collector()
    collector.save_to_parquet(_candles("2026-07-01", 24, volume=1.0), "BTCUSDT")

    # La candela delle 23:00 arriva di nuovo, stavolta completa (volume 5)
    final = _candles("2026-07-01 23:00", 1, volume=5.0)
    with patch.object(collector, "fetch_ohlcv", side_effect=[final, pd.DataFrame()]):
        df = collector.update_historical("BTC/USDT", "BTCUSDT")

    assert len(df) == 24  # nessuna riga aggiunta, solo sostituita
    assert df.loc[pd.Timestamp("2026-07-01 23:00"), "volume"] == 5.0


def test_missing_parquet_triggers_full_download(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    collector = _make_collector()
    full = _candles("2026-07-01", 48)

    with patch.object(collector, "download_historical", return_value=full) as dl:
        df = collector.update_historical("BTC/USDT", "BTCUSDT")

    dl.assert_called_once_with("BTC/USDT", "1h", days=HISTORY_DAYS)
    assert len(df) == 48
    assert len(collector.load_historical("BTCUSDT")) == 48


def test_no_new_candles_keeps_parquet_untouched(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    collector = _make_collector()
    collector.save_to_parquet(_candles("2026-07-01", 24), "BTCUSDT")

    with patch.object(collector, "fetch_ohlcv", return_value=pd.DataFrame()), \
         patch.object(collector, "save_to_parquet") as save:
        df = collector.update_historical("BTC/USDT", "BTCUSDT")

    assert len(df) == 24
    save.assert_not_called()
