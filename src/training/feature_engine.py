import pandas as pd
import numpy as np

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['returns'] = df['close'].pct_change()
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain / loss))
    
    # SMA
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()
    
    # Volatilità
    df['volatility'] = df['returns'].rolling(20).std() * np.sqrt(252)
    
    # Momentum
    df['momentum'] = df['close'].pct_change(10)
    
    # Volume ratio
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
    
    # Price SMA20 ratio
    df['price_sma20'] = df['close'] / df['sma_20'] - 1
    
    # ---- NUOVE FEATURE ----
    
    # ATR
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['atr'] = true_range.rolling(14).mean()
    df['atr_pct'] = df['atr'] / df['close']
    
    # Bollinger Bands
    df['bb_middle'] = df['close'].rolling(20).mean()
    bb_std = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_middle'] + 2 * bb_std
    df['bb_lower'] = df['bb_middle'] - 2 * bb_std
    df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-6)
    
    # MACD
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    df['macd_hist_norm'] = df['macd_hist'] / df['close']
    
    # OBV
    df['obv'] = (np.sign(df['close'].diff()) * df['volume']).cumsum()
    df['obv_norm'] = df['obv'] / df['obv'].rolling(50).mean() - 1
    
    # Fibonacci
    high_20 = df['high'].rolling(20).max()
    low_20 = df['low'].rolling(20).min()
    diff_20 = high_20 - low_20
    df['fib_position'] = (df['close'] - low_20) / (diff_20 + 1e-6)
    fib_618 = low_20 + 0.618 * diff_20
    df['fib_618_distance'] = (df['close'] - fib_618) / df['close']
    
    # Target
    df['target'] = (df['close'].shift(-5) / df['close'] - 1 > 0.005).astype(int)
    
    df = df.dropna()
    return df

def prepare_train_data(df: pd.DataFrame) -> tuple:
    df = add_features(df)
    feature_cols = [
        'rsi', 'sma_20', 'sma_50', 'volatility', 'momentum',
        'volume_ratio', 'price_sma20', 'returns',
        'atr_pct', 'bb_position',
        'macd_hist_norm', 'obv_norm', 'fib_position', 'fib_618_distance'
    ]
    X = df[feature_cols]
    y = df['target']
    return X, y
