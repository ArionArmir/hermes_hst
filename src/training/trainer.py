import pandas as pd
from sklearn.metrics import accuracy_score
import joblib
import os
import shutil
from datetime import datetime, timezone
from loguru import logger
import redis
from src.training.feature_engine import prepare_train_data
from src.training.model_fit import fit_model
from src.backtest import BacktestParams, backtest_joint

class Trainer:
    def __init__(self):
        self.redis = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.model_path = "config/models/champion.pkl"
        self.challenger_path = "config/models/challenger.pkl"

    def train(self, X_train: pd.DataFrame, X_val: pd.DataFrame, y_train: pd.Series, y_val: pd.Series,
              X_calib: pd.DataFrame = None, y_calib: pd.Series = None, val_candles: dict = None,
              backtest_params: BacktestParams = None):
        """Addestra un nuovo modello XGBoost su train/validation già pronti e già
        divisi (Approccio A: possono provenire dalla concatenazione di più
        simboli). Lo split va fatto PRIMA per singolo simbolo (rispettando
        l'ordine temporale di ciascuno) e poi concatenato separatamente per
        train e per validation — uno split unico sul dataset già concatenato
        con shuffle=False finirebbe per validare quasi solo sull'ultimo
        simbolo appeso, non su un campione rappresentativo di tutti.

        X_calib/y_calib: terzo split, cronologicamente tra train e val, usato
        SOLO per early stopping e calibrazione delle probabilità (mai per il
        confronto champion/challenger, che resta su val — altrimenti la
        stessa fetta di dati influenzerebbe sia il fit sia la promozione).

        val_candles: {symbol: OHLCV del periodo di validation} — se presente,
        champion e challenger si confrontano sul PnL NETTO del backtest
        (fee e slippage inclusi), non sull'accuratezza: l'accuratezza premia
        chi indovina le candele, il backtest chi fa soldi con la strategia
        reale (docs/IMPROVEMENT_PLAN.md, M3).

        backtest_params: parametri del backtest di confronto (soglia, ATR,
        cap direzionale, circuit breaker) — deve rispecchiare la config di
        produzione, altrimenti si promuove un modello sul PnL di una
        strategia diversa da quella davvero live (train_all_models.py la
        costruisce dallo stesso YAML letto dall'engine)."""
        logger.info(f"🧠 Avvio training su {len(X_train)} righe (validation: {len(X_val)})...")

        if len(X_train) < 100:
            logger.error("❌ Dati insufficienti per training")
            return False

        distribution = y_train.value_counts(normalize=True).sort_index()
        logger.info(f"📊 Distribuzione classi train (down/flat/up): {distribution.round(3).to_dict()}")

        model, fit_info = fit_model(X_train, y_train, X_calib, y_calib)
        if fit_info["calibrated"]:
            logger.info(
                f"🎯 Probabilità calibrate (Platt/sigmoid) sul set di calibrazione "
                f"({fit_info['n_trees']} alberi, early stopping)"
            )
        else:
            logger.warning(
                "⚠️ Calibrazione saltata (set di calibrazione assente o troppo piccolo): "
                "le probabilità potrebbero non essere affidabili per la soglia configurata"
            )

        acc = accuracy_score(y_val, model.predict(X_val))
        logger.info(f"✅ Accuratezza: {acc:.2%}")

        joblib.dump(model, self.challenger_path)
        logger.info(f"💾 Challenger salvato: {self.challenger_path}")

        # Backtest del CHALLENGER, calcolato una sola volta e riusato sia per
        # il confronto con il champion sia per pubblicare le metriche attese
        # se viene promosso (docs/IMPROVEMENT_PLAN.md, V4/N4: il watchdog le
        # confronta col comportamento live per rilevare un degrado).
        challenger_bt = backtest_joint(model, val_candles, backtest_params) if val_candles else None

        if os.path.exists(self.model_path):
            champion = joblib.load(self.model_path)
            try:
                # Un champion con classi diverse (es. il vecchio binario) non
                # solleverebbe eccezioni su predict, ma il confronto di
                # accuratezza sarebbe privo di senso: trattalo come incompatibile.
                if list(champion.classes_) != list(model.classes_):
                    raise ValueError(
                        f"classi champion {list(champion.classes_)} != challenger {list(model.classes_)}"
                    )
                promote, verdict = self._compare_models(model, champion, X_val, y_val, acc, val_candles,
                                                        backtest_params, challenger_bt)
            except Exception as e:
                # Il champion è stato addestrato su un set di feature diverso
                # (nomi/ordine non compatibili con FEATURE_COLS attuale): non è
                # confrontabile né utilizzabile in inference, promuovo il
                # challenger direttamente.
                logger.warning(f"⚠️ Champion incompatibile con le feature attuali ({e}), promuovo il challenger")
                self._swap_model(challenger_bt)
                logger.info("🏆 Challenger promosso per incompatibilità del champion")
                return True
            if promote:
                self._swap_model(challenger_bt)
                logger.info(f"🏆 Nuovo champion! ({verdict})")
            else:
                logger.info(f"ℹ️ Challenger non supera champion ({verdict})")
        else:
            self._swap_model(challenger_bt)
            logger.info("🏆 Primo modello champion")

        return True

    def _compare_models(self, challenger, champion, X_val, y_val, challenger_acc, val_candles,
                       backtest_params: BacktestParams = None, challenger_bt=None):
        """(promuovere?, motivazione). Preferisce il backtest a portafoglio
        condiviso (backtest_joint: stesso capitale e cap di margine tra
        simboli, come l'engine live — la promozione deve riflettere lo
        stesso rischio di correlazione a cui il sistema è davvero esposto,
        non il PnL ottimistico di simboli simulati indipendentemente).
        Ripiega sull'accuratezza solo se le candele di validation non sono
        disponibili o non producono risultati."""
        if val_candles:
            if challenger_bt is None:
                challenger_bt = backtest_joint(challenger, val_candles, backtest_params)
            champion_bt = backtest_joint(champion, val_candles, backtest_params)
            if challenger_bt is not None and champion_bt is not None:
                logger.info(
                    f"🔬 Backtest validation — challenger: PnL netto {challenger_bt.net_pnl:.2f} USDT "
                    f"({challenger_bt.n_trades} trade, hit {challenger_bt.hit_rate:.0%}, "
                    f"maxDD {challenger_bt.max_drawdown_pct:.1%}) | champion: {champion_bt.net_pnl:.2f} USDT "
                    f"({champion_bt.n_trades} trade, hit {champion_bt.hit_rate:.0%}, "
                    f"maxDD {champion_bt.max_drawdown_pct:.1%})"
                )
                verdict = (f"PnL netto backtest: {challenger_bt.net_pnl:.2f} vs "
                           f"{champion_bt.net_pnl:.2f} USDT")
                return challenger_bt.net_pnl > champion_bt.net_pnl, verdict
            logger.warning("⚠️ Backtest senza risultati, ripiego sul confronto di accuratezza")
        champion_acc = accuracy_score(y_val, champion.predict(X_val))
        return challenger_acc > champion_acc, f"accuratezza {challenger_acc:.2%} vs {champion_acc:.2%}"

    def _swap_model(self, validation_result=None):
        """Swap atomico su Redis. Se disponibile un BacktestResult di
        validation del nuovo champion, ne pubblica le metriche attese (hit
        rate, PnL netto) — il watchdog le confronta col comportamento live
        recente per rilevare un modello che si degrada gradualmente
        (docs/IMPROVEMENT_PLAN.md, V4/N4): un evento diverso da una crisi
        acuta (già coperta dal circuit breaker), non necessariamente
        abbastanza brusco da far scattare quello."""
        shutil.copy(self.challenger_path, self.model_path)
        self.redis.set('active_model_path', self.model_path)
        self.redis.publish('model_swap', self.model_path)
        if validation_result is not None:
            self.redis.set('champion_hit_rate', str(validation_result.hit_rate))
            self.redis.set('champion_net_pnl', str(validation_result.net_pnl))
            self.redis.set('champion_promoted_at', datetime.now(timezone.utc).isoformat())
        logger.info("🔄 Modello swapped via Redis")

# Il punto d'ingresso per addestrare su tutti i simboli configurati è
# train_all_models.py (repo root): concatena le feature di ogni simbolo in
# config/trading_params.yaml e addestra un unico champion (Approccio A).
