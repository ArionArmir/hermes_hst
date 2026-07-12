import sys
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.inference.feature_engine import FeatureEngine

# Simula dati reali (50 tick)
fe = FeatureEngine(window=100)
for i in range(50):
    price = 60000 + i * 10 + np.random.randn() * 50
    volume = 1 + np.random.rand() * 5
    fe.add_tick(price, volume)

# Calcola features
features = fe.calculate_features()

print(f"Type: {type(features)}")
print(f"isinstance np.ndarray: {isinstance(features, np.ndarray)}")
if isinstance(features, np.ndarray):
    print(f"ndim: {features.ndim}")
    print(f"shape: {features.shape}")
    print(f"dtype: {features.dtype}")
    print(f"size: {features.size}")
    print(f"values: {features}")
else:
    print(f"values: {features}")
