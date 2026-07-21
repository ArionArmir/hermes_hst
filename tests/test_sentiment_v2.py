"""Sentiment v2 (src/sentiment/v2.py) — le funzioni pure.

La v2 verrà giudicata su criteri scritti prima (docs/CRITERI_SENTIMENT_V2.md):
decadimento, novità, guardia anti-degenerazione e parsing sono i mattoni di
quel giudizio.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sentiment.v2 import combina, decadi, degenere, estrai_score, novita

ADESSO = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def test_decadimento_mezza_vita():
    assert abs(decadi(0.8, 360) - 0.4) < 1e-9        # una mezza vita
    assert abs(decadi(0.8, 720) - 0.2) < 1e-9        # due
    assert decadi(0.8, 0) == 0.8


def test_novita_riconosce_titoli_gia_visti():
    nuovi, viste = novita(["Bitcoin crolla", "ETF approvato"], {}, ADESSO)
    assert len(nuovi) == 2
    dopo, viste = novita(["bitcoin CROLLA  ", "Notizia inedita"], viste, ADESSO)
    assert dopo == ["Notizia inedita"]               # case/spazi non contano


def test_novita_pota_la_memoria_vecchia():
    _, viste = novita(["vecchia"], {}, ADESSO - timedelta(days=10))
    nuovi, viste = novita(["vecchia"], viste, ADESSO)
    assert len(nuovi) == 1                           # dimenticata dopo 7 giorni


def test_novita_per_asset():
    """Una notizia di mercato vista per BTC resta nuova per ETH: la novità
    vive nello spazio dell'asset, non globale."""
    nuovi, viste = novita(["Fed alza i tassi"], {}, ADESSO, spazio="BTC")
    assert len(nuovi) == 1
    nuovi, viste = novita(["Fed alza i tassi"], viste, ADESSO, spazio="ETH")
    assert len(nuovi) == 1
    nuovi, _ = novita(["Fed alza i tassi"], viste, ADESSO, spazio="BTC")
    assert nuovi == []


def test_combina_e_clamp():
    import pytest
    assert combina(0.4, 0.8) == pytest.approx(0.6)
    assert combina(-1.0, -1.0) == -1.0


def test_estrai_score():
    assert estrai_score('{"score": -0.7}') == -0.7
    assert estrai_score('{"score": 5}') is None      # fuori range
    assert estrai_score("non json") is None
    assert estrai_score('{"altro": 1}') is None


def test_degenere_identici_e_scala():
    assert degenere({"A": 0.5, "B": 0.5, "C": 0.5})
    assert degenere({"A": -0.8, "B": -0.7, "C": -0.6, "D": -0.5})   # la scala di v1
    assert not degenere({"A": 0.0, "B": 0.0, "C": 0.0})             # zeri: non degenere
    assert not degenere({"A": 0.3, "B": -0.2, "C": 0.7})


def test_passo_macro_stati_e_novita():
    """Canale macro: stessa grammatica della v2 (novità nello spazio MACRO,
    decadimento, stati dichiarati), valutatore iniettato — senza Ollama."""
    import asyncio
    from src.sentiment.macro import passo_macro

    async def valuta_fisso(nuovi):
        return -0.6

    async def scenario():
        # prima volta: notizie nuove → stato nuovo, blend 50/50 con 0
        r1, viste = await passo_macro(["[BCE] Rate decision"], None, {}, ADESSO,
                                      valuta_fisso)
        assert r1["stato"] == "nuovo" and r1["score"] == -0.3
        # stesso titolo dopo: niente di nuovo → decade
        prec = {"score": r1["score"], "ts": ADESSO.isoformat()}
        r2, viste = await passo_macro(["[BCE] Rate decision"], prec, viste,
                                      ADESSO + timedelta(hours=6), valuta_fisso)
        assert r2["stato"] == "decaduto" and abs(r2["score"] - (-0.15)) < 1e-9
        # valutatore che esplode → errore, si tiene il decaduto
        async def valuta_rotto(nuovi):
            raise RuntimeError("giù")
        r3, _ = await passo_macro(["[SEC] Nuova notizia"], prec, viste,
                                  ADESSO + timedelta(hours=6), valuta_rotto)
        assert r3["stato"] == "errore" and abs(r3["score"] - (-0.15)) < 1e-9

    asyncio.run(scenario())


def test_titoli_macro_prefissati_e_spazio_separato():
    """La memoria macro non interferisce con quella per-asset: stesso titolo,
    spazi diversi, novità indipendenti."""
    from src.sentiment.v2 import novita
    from src.sentiment.macro import SPAZIO_VISTE
    nuovi, viste = novita(["Rate decision"], {}, ADESSO, spazio=SPAZIO_VISTE)
    assert len(nuovi) == 1
    nuovi_btc, _ = novita(["Rate decision"], viste, ADESSO, spazio="BTC")
    assert len(nuovi_btc) == 1


def test_decadimento_clampa_minuti_negativi():
    """Orologio all'indietro (NTP/sleep WSL2): mai amplificare (S4)."""
    assert decadi(0.8, -120) == 0.8


def test_stato_corrotto_non_crasha_e_scrittura_atomica(tmp_path, monkeypatch):
    """Un crash a metà scrittura non deve produrre un crash-loop (S1)."""
    import src.sentiment.v2 as v2mod
    monkeypatch.setattr(v2mod, "DIR_STATO", tmp_path)
    (tmp_path / "stato.json").write_text('{"scores": {truncated')
    s = v2mod.SentimentV2.__new__(v2mod.SentimentV2)
    stato = v2mod.SentimentV2._carica_stato(s)
    assert stato == {"scores": {}, "viste": {}}          # ripartenza pulita
    assert list(tmp_path.glob("stato.corrotto.*"))       # autopsia conservata
    s.stato = {"scores": {"BTC": {"score": 0.1, "ts": ADESSO.isoformat()}}, "viste": {}}
    v2mod.SentimentV2._salva_stato(s)
    import json as _json
    assert _json.loads((tmp_path / "stato.json").read_text()) == s.stato
    assert not (tmp_path / "stato.tmp").exists()         # niente residui


def test_dimentica_restituisce_i_titoli(monkeypatch):
    """Valutazione fallita → i titoli tornano 'mai visti' (S3)."""
    from src.sentiment.v2 import dimentica
    nuovi, viste = novita(["SEC approva ETF SOL"], {}, ADESSO, spazio="SOL")
    assert len(nuovi) == 1
    viste = dimentica(nuovi, viste, spazio="SOL")
    di_nuovo, _ = novita(["SEC approva ETF SOL"], viste, ADESSO, spazio="SOL")
    assert len(di_nuovo) == 1                            # riacquisibile


def test_passo_macro_errore_non_brucia_i_titoli():
    """La stessa garanzia S3 sul canale macro."""
    import asyncio
    from src.sentiment.macro import passo_macro, SPAZIO_VISTE

    async def rotto(nuovi):
        raise RuntimeError("Ollama giù")

    async def buono(nuovi):
        return -0.4

    async def scenario():
        r1, viste = await passo_macro(["[SEC] Enforcement"], None, {}, ADESSO, rotto)
        assert r1["stato"] == "errore"
        r2, _ = await passo_macro(["[SEC] Enforcement"], None, viste, ADESSO, buono)
        assert r2["stato"] == "nuovo" and r2["fresco"] == -0.4   # rivalutata!

    asyncio.run(scenario())
