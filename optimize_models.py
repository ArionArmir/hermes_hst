#!/usr/bin/env python
"""
Ottimizzazione dei parametri per:
- Modello di Uscita (ATR) → atr_multiplier_sl, atr_multiplier_tp, atr_period
- Modello di Conferma (Volume & Pattern) → threshold_confirm, threshold_reject, pattern_window
"""
import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
from datetime import datetime, timedelta
from loguru import logger
from src.exit_model import ATRExitModel
from src.volume_pattern import VolumePatternAnalyzer
import random

class ModelOptimizer:
    def __init__(self, symbol="BTC-USD", period="1y"):
        self.symbol = symbol
        self.period = period
        self.results = []

    def load_data(self):
        """Carica dati storici da Yahoo Finance"""
        logger.info(f"📥 Caricamento dati per {self.symbol} ({self.period})...")
        try:
            end = datetime.now()
            start = end - timedelta(days=365 if self.period == "1y" else 180)
            ticker = yf.Ticker(self.symbol)
            df = ticker.history(start=start, end=end, interval="1h")
            if df.empty:
                logger.error("❌ Nessun dato caricato da Yahoo")
                return None
            # Rinomina colonne per compatibilità
            df.columns = ['open', 'high', 'low', 'close', 'volume', 'dividends', 'splits']
            df = df.drop(columns=['dividends', 'splits'])
            df.rename(columns={'close': self.symbol}, inplace=True)
            # Aggiunge volume in float
            df['volume'] = df['volume'].astype(float)
            logger.info(f"✅ Caricati {len(df)} dati")
            return df
        except Exception as e:
            logger.error(f"❌ Errore caricamento dati: {e}")
            return None

    def simulate_trades(self, df, atr_sl, atr_tp, atr_period, threshold_confirm, threshold_reject, pattern_window):
        """Simula trading con i parametri dati"""
        capital = 1000.0
        position = None
        trades = []

        exit_model = ATRExitModel(atr_multiplier_sl=atr_sl, atr_multiplier_tp=atr_tp)
        exit_model.window = atr_period
        pattern_model = VolumePatternAnalyzer(window=pattern_window)

        df['sma20'] = df[self.symbol].rolling(20).mean()
        df['sma50'] = df[self.symbol].rolling(50).mean()
        df['signal'] = 0
        df.loc[df['sma20'] > df['sma50'], 'signal'] = 1
        df.loc[df['sma20'] < df['sma50'], 'signal'] = -1

        for i in range(60, len(df)):
            price = df[self.symbol].iloc[i]
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            volume = df['volume'].iloc[i]
            signal = df['signal'].iloc[i]

            exit_model.add_price(price, high, low)
            pattern_model.add_data(price, volume, high, low)

            pattern_result = pattern_model.analyze()
            pattern_score = pattern_result["score"]

            if pattern_score > threshold_confirm:
                signal_filtered = signal
            elif pattern_score < threshold_reject:
                signal_filtered = 0
            else:
                signal_filtered = signal

            if position is None and signal_filtered != 0:
                side = 'long' if signal_filtered == 1 else 'short'
                sl, tp = exit_model.calculate_exit_levels(price, side)
                position = {
                    'entry': price,
                    'side': side,
                    'sl': sl,
                    'tp': tp
                }
            elif position is not None:
                if position['side'] == 'long':
                    if price <= position['sl']:
                        trades.append({'pnl': (price - position['entry'])})
                        position = None
                    elif price >= position['tp']:
                        trades.append({'pnl': (price - position['entry'])})
                        position = None
                else:
                    if price >= position['sl']:
                        trades.append({'pnl': (position['entry'] - price)})
                        position = None
                    elif price <= position['tp']:
                        trades.append({'pnl': (position['entry'] - price)})
                        position = None

        if trades:
            df_trades = pd.DataFrame(trades)
            pnl_total = df_trades['pnl'].sum()
            win_rate = (df_trades['pnl'] > 0).mean() * 100
            avg_pnl = df_trades['pnl'].mean()
            sharpe = df_trades['pnl'].mean() / df_trades['pnl'].std() if len(df_trades) > 1 else 0
            total_trades = len(df_trades)
        else:
            pnl_total, win_rate, avg_pnl, sharpe, total_trades = 0, 0, 0, 0, 0

        return {
            'pnl_total': pnl_total,
            'win_rate': win_rate,
            'avg_pnl': avg_pnl,
            'sharpe': sharpe,
            'total_trades': total_trades,
            'atr_sl': atr_sl,
            'atr_tp': atr_tp,
            'atr_period': atr_period,
            'threshold_confirm': threshold_confirm,
            'threshold_reject': threshold_reject,
            'pattern_window': pattern_window
        }

    def optimize(self, df):
        logger.info("🚀 Avvio ottimizzazione dei parametri...")
        atr_sl_range = np.arange(1.0, 4.0, 0.5)
        atr_tp_range = np.arange(2.0, 6.0, 0.5)
        atr_period_range = [10, 14, 20]
        threshold_confirm_range = [0.2, 0.3, 0.4]
        threshold_reject_range = [-0.2, -0.3, -0.4]
        pattern_window_range = [10, 20, 30]

        param_combinations = list(product(
            atr_sl_range,
            atr_tp_range,
            atr_period_range,
            threshold_confirm_range,
            threshold_reject_range,
            pattern_window_range
        ))

        if len(param_combinations) > 100:
            param_combinations = random.sample(param_combinations, 100)

        logger.info(f"📊 Test di {len(param_combinations)} combinazioni...")
        for params in param_combinations:
            result = self.simulate_trades(df, *params)
            self.results.append(result)

        df_results = pd.DataFrame(self.results)
        df_results = df_results.sort_values('sharpe', ascending=False)
        return df_results

if __name__ == "__main__":
    optimizer = ModelOptimizer(symbol="BTC-USD", period="1y")
    df = optimizer.load_data()
    if df is not None:
        results = optimizer.optimize(df)
        print("\n🏆 TOP 10 COMBINAZIONI (per Sharpe):")
        print(results.head(10).to_string(index=False))
        if not results.empty:
            best = results.iloc[0]
            print("\n✅ Migliori parametri trovati:")
            print(f"   ATR_SL: {best['atr_sl']:.1f}")
            print(f"   ATR_TP: {best['atr_tp']:.1f}")
            print(f"   ATR_PERIOD: {best['atr_period']:.0f}")
            print(f"   THRESHOLD_CONFIRM: {best['threshold_confirm']:.1f}")
            print(f"   THRESHOLD_REJECT: {best['threshold_reject']:.1f}")
            print(f"   PATTERN_WINDOW: {best['pattern_window']:.0f}")
            print(f"   PnL: {best['pnl_total']:.2f}")
            print(f"   Sharpe: {best['sharpe']:.2f}")
        results.to_csv("optimization_results.csv", index=False)
        print("💾 Risultati salvati in optimization_results.csv")
