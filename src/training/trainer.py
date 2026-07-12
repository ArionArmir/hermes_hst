import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import os
import shutil
from datetime import datetime
from loguru import logger
import redis
from src.training.feature_engine import prepare_train_data

class Trainer:
    def __init__(self):
        self.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.redis = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.model_path = "config/models/champion.pkl"
        self.challenger_path = "config/models/challenger.pkl"

    def train(self, df: pd.DataFrame, symbol: str = "BTCUSDT"):
        """Addestra un nuovo modello XGBoost"""
        logger.info(f"🧠 Avvio training su {symbol}...")
        X, y = prepare_train_data(df)

        if len(X) < 100:
            logger.error("❌ Dati insufficienti per training")
            return False

        X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42, shuffle=False)

        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            objective='binary:logistic',
            eval_metric='logloss',
            random_state=42
        )
        model.fit(X_train, y_train)

        acc = accuracy_score(y_val, model.predict(X_val))
        logger.info(f"✅ Accuratezza: {acc:.2%}")

        joblib.dump(model, self.challenger_path)
        logger.info(f"💾 Challenger salvato: {self.challenger_path}")

        if os.path.exists(self.model_path):
            champion = joblib.load(self.model_path)
            champion_acc = accuracy_score(y_val, champion.predict(X_val))
            if acc > champion_acc:
                self._swap_model()
                logger.info(f"🏆 Nuovo champion! {acc:.2%} > {champion_acc:.2%}")
            else:
                logger.info(f"ℹ️ Challenger non supera champion ({acc:.2%} < {champion_acc:.2%})")
        else:
            self._swap_model()
            logger.info("🏆 Primo modello champion")

        return True

    def _swap_model(self):
        """Swap atomico su Redis"""
        shutil.copy(self.challenger_path, self.model_path)
        self.redis.set('active_model_path', self.model_path)
        self.redis.publish('model_swap', self.model_path)
        logger.info("🔄 Modello swapped via Redis")

if __name__ == "__main__":
    from src.data_collector import DataCollector

    collector = DataCollector()
    trainer = Trainer()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    for symbol in symbols:
        symbol_clean = symbol.replace('/', '')
        df = collector.load_historical(symbol_clean, timeframe="1h")
        if df.empty:
            logger.info(f"📥 Scarico {symbol}...")
            df = collector.download_historical(symbol, timeframe='1h', days=365)
            collector.save_to_parquet(df, symbol_clean, timeframe="1h")
        if not df.empty:
            trainer.train(df, symbol=symbol_clean)
        else:
            logger.error(f"❌ Impossibile scaricare {symbol}")
