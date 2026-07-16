"""
Sigillo dell'holdout e registro dei tentativi.

L'holdout è l'unica risorsa non rinnovabile del progetto: si consuma
guardandolo, e una volta consumato nessun dato storico può sostituirlo
(validare in avanti richiederebbe 5-85 anni di paper trading). Il guardiano
deve quindi fallire rumorosamente, mai silenziosamente.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared import holdout as H
from src.shared.holdout import HoldoutViolation, deflated_sharpe_ratio


@pytest.fixture
def sigillo(tmp_path, monkeypatch):
    """Manifesto e dati sigillati finti: i test non devono toccare quelli veri."""
    hdir = tmp_path / "data" / "holdout"
    hdir.mkdir(parents=True)
    idx = pd.date_range("2024-01-01", periods=100, freq="1h")
    for sym in ("BCHUSDT", "ETCUSDT", "EOSUSDT", "XLMUSDT"):
        pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0,
                      "volume": 10.0, "n_trades": 5.0, "taker_buy_base": 5.0},
                     index=idx).to_parquet(hdir / f"{sym}_1h.parquet")
    manifest = tmp_path / "config" / "holdout.yaml"
    manifest.parent.mkdir(parents=True)
    with open(manifest, "w") as f:
        yaml.safe_dump({
            "sigillato_il": "2026-07-16",
            "lotti": {
                "A": {"stato": "SIGILLATO", "aperto_da": None,
                      "simboli": {"BCHUSDT": {"barre": 100}, "ETCUSDT": {"barre": 100}}},
                "B": {"stato": "SIGILLATO", "aperto_da": None,
                      "simboli": {"EOSUSDT": {"barre": 100}, "XLMUSDT": {"barre": 100}}},
            }}, f)
    monkeypatch.setattr(H, "HOLDOUT_DIR", hdir)
    monkeypatch.setattr(H, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(H, "REGISTRY_PATH", tmp_path / "docs" / "registry.jsonl")
    return tmp_path


def test_ricerca_su_simbolo_sigillato_solleva(sigillo):
    # Il caso che il modulo esiste per impedire: uno script di tuning che
    # include per distrazione un simbolo dell'holdout
    with pytest.raises(HoldoutViolation, match="HOLDOUT"):
        H.assert_research_allowed(["BTCUSDT", "BCHUSDT"])


def test_lotti_separati(sigillo):
    assert H.lots() == ["A", "B"]
    assert H.sealed_symbols("A") == ["BCHUSDT", "ETCUSDT"]
    assert H.sealed_symbols("B") == ["EOSUSDT", "XLMUSDT"]
    assert H.sealed_symbols() == ["BCHUSDT", "EOSUSDT", "ETCUSDT", "XLMUSDT"]
    assert H.sealed_lots() == ["A", "B"]


def test_aprire_un_lotto_non_consuma_l_altro(sigillo):
    """Il motivo per cui i lotti esistono: due colpi invece di uno."""
    H.open_seal("A", "soglia 0.50 batte 0.55", n_trials=41, motivazione="predetta dai bucket")
    assert H.lot_status("A") == "APERTO"
    assert H.lot_status("B") == "SIGILLATO"
    assert H.sealed_lots() == ["B"]
    # La seconda cartuccia è ancora sparabile, su un'ipotesi diversa
    dati = H.open_seal("B", "target ridefinito", n_trials=1, motivazione="nuova ipotesi")
    assert set(dati) == {"EOSUSDT", "XLMUSDT"}


def test_ricerca_vietata_anche_su_lotto_gia_aperto(sigillo):
    """Un lotto speso non torna pulito: usarlo per cercare e poi citarne il
    risultato come validazione sarebbe riciclaggio."""
    H.open_seal("A", "ipotesi", n_trials=41, motivazione="x")
    with pytest.raises(HoldoutViolation, match="HOLDOUT"):
        H.assert_research_allowed(["BCHUSDT"])


def test_lotto_inesistente_solleva(sigillo):
    with pytest.raises(HoldoutViolation, match="inesistente"):
        H.open_seal("Z", "ipotesi", n_trials=1, motivazione="x")


def test_ricerca_su_universo_operativo_passa(sigillo):
    H.assert_research_allowed(["BTCUSDT", "ETHUSDT", "DOGEUSDT"])  # non solleva


def test_registro_conta_anche_i_tentativi_perdenti(sigillo):
    # Contare solo i vincitori produrrebbe una correzione che non corregge
    H.record_trial("fam", {"soglia": 0.55}, {"pnl": -8.43})
    H.record_trial("fam", {"soglia": 0.50}, {"pnl": +244.65})
    H.record_trial("altra", {"x": 1}, {"pnl": 0})
    assert H.count_trials("fam") == 2
    assert H.count_trials() == 3


def test_registro_vuoto_conta_zero(sigillo):
    assert H.count_trials() == 0


def test_dsr_scende_al_crescere_dei_tentativi():
    rng = np.random.default_rng(0)
    pnl = rng.normal(0.278, 4.271, 880)      # il nostro candidato
    d1, d6, d41 = (deflated_sharpe_ratio(pnl, n) for n in (1, 6, 41))
    assert d1 > d6 > d41
    # Con un tiro solo sembra promuovibile; col conteggio onesto non lo è più.
    # È l'intero motivo per cui il registro esiste: sui dati reali del
    # 2026-07-16 lo stesso candidato passa da 97.3% (1 tiro) a 21.4% (41).
    assert d1 - d41 > 0.30
    assert d41 < 0.60


def test_dsr_su_rumore_puro_non_promuove():
    rng = np.random.default_rng(1)
    pnl = rng.normal(0.0, 4.271, 880)        # edge vero = zero
    assert deflated_sharpe_ratio(pnl, 41) < 0.90


def test_apertura_registra_ipotesi_e_tentativi(sigillo):
    dati = H.open_seal("A", "soglia 0.50 batte 0.55", n_trials=41,
                       motivazione="predetta dai bucket")
    assert set(dati) == {"BCHUSDT", "ETCUSDT"}
    assert len(dati["BCHUSDT"]) == 100
    man = yaml.safe_load(open(H.MANIFEST_PATH))
    assert man["lotti"]["A"]["stato"] == "APERTO"
    assert man["lotti"]["A"]["aperto_da"]["tentativi_dichiarati"] == 41
    assert H.count_trials("APERTURA_HOLDOUT") == 1


def test_riapertura_stesso_lotto_bloccata(sigillo):
    H.open_seal("A", "prima ipotesi", n_trials=41, motivazione="x")
    # Riaprire per una seconda selezione rende il lotto un set di ricerca:
    # deve costare una scelta esplicita, non capitare per distrazione
    with pytest.raises(HoldoutViolation, match="GIÀ APERTO"):
        H.open_seal("A", "seconda ipotesi", n_trials=3, motivazione="y")


def test_riapertura_consapevole_permessa(sigillo):
    H.open_seal("A", "prima", n_trials=41, motivazione="x")
    dati = H.open_seal("A", "seconda", n_trials=3, motivazione="y", acknowledge_burned=True)
    assert set(dati) == {"BCHUSDT", "ETCUSDT"}


def test_esauriti_i_lotti_il_messaggio_lo_dice(sigillo):
    """Quando finiscono le cartucce non ci sono alternative: l'errore deve
    dirlo, perché il passo successivo (paper trading) costa mesi."""
    H.open_seal("A", "prima", n_trials=41, motivazione="x")
    H.open_seal("B", "seconda", n_trials=1, motivazione="y")
    assert H.sealed_lots() == []
    with pytest.raises(HoldoutViolation, match="NESSUN lotto carico"):
        H.open_seal("B", "terza", n_trials=1, motivazione="z")


def test_manifesto_mancante_solleva(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "MANIFEST_PATH", tmp_path / "assente.yaml")
    with pytest.raises(HoldoutViolation, match="Nessun sigillo"):
        H.sealed_symbols()
