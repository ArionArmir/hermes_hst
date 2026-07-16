"""
Logica pura di walk_forward.py: i confini dei fold devono essere
crescenti, coprire l'ultimo 60% dello storico comune a tutti i simboli e
non dipendere dall'ordine con cui i simboli sono passati.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from walk_forward import build_fold_boundaries


def _df_with_range(start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="1h")
    return pd.DataFrame({"close": 1.0}, index=idx)


def test_boundaries_count_and_monotonic():
    raw = {"A": _df_with_range("2026-01-01", "2026-12-31")}
    boundaries = build_fold_boundaries(raw, n_folds=4)

    assert len(boundaries) == 5
    assert all(boundaries[i] < boundaries[i + 1] for i in range(4))


def test_boundaries_use_common_range_across_symbols():
    # "B" ha uno storico più corto: il range comune deve restringersi al suo
    raw = {
        "A": _df_with_range("2026-01-01", "2026-12-31"),
        "B": _df_with_range("2026-03-01", "2026-09-30"),
    }
    boundaries = build_fold_boundaries(raw, n_folds=3)

    assert boundaries[0] >= pd.Timestamp("2026-03-01")
    assert boundaries[-1] <= pd.Timestamp("2026-09-30")


def test_first_boundary_leaves_at_least_40_percent_for_training():
    raw = {"A": _df_with_range("2026-01-01", "2027-01-01")}
    boundaries = build_fold_boundaries(raw, n_folds=5)

    start, end = raw["A"].index.min(), raw["A"].index.max()
    fraction_before_first_fold = (boundaries[0] - start) / (end - start)
    assert abs(fraction_before_first_fold - 0.4) < 1e-6
