# Sviluppo

Guida per chi contribuisce al codice di Hermes HFT: come preparare l'ambiente, la struttura del repository, come testare, le convenzioni di codice e gli strumenti di debug.

## Indice

- [Ambiente di sviluppo](#ambiente-di-sviluppo)
- [Struttura delle cartelle](#struttura-delle-cartelle)
- [Test](#test)
- [Convenzioni di codice](#convenzioni-di-codice)
- [Gestione del versionamento](#gestione-del-versionamento)
- [Debug](#debug)

## Ambiente di sviluppo

Il progetto è sviluppato e pensato per girare su **Linux** (nativamente o via **WSL2** su Windows), perché si appoggia a `redis-server`, `taskset` (affinità CPU per l'Engine) e a un event loop `uvloop` che non è disponibile su Windows nativo.

1. **WSL2** (solo su Windows): installa una distribuzione Ubuntu recente (`wsl --install -d Ubuntu`) e lavora sempre dentro quel filesystem Linux (es. `~/hermes_hft`), non su `/mnt/c/...`, per evitare problemi di performance e permessi.
2. **Python 3.12+**: verifica con `python3 --version`. Su Ubuntu/WSL: `sudo apt install python3.12 python3.12-venv`.
3. **Virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Redis**: `sudo apt install redis-server`, poi `sudo service redis-server start`. Verifica con `redis-cli ping` (deve rispondere `PONG`).
5. **Ollama** (necessario solo per lavorare sul servizio Sentiment): installa da [ollama.com](https://ollama.com), poi `ollama serve` e `ollama pull qwen2.5-coder:1.5b` (il modello usato di default in `src/sentiment/ollama_client.py`).
6. **Editor/IDE**: qualunque editor va bene; se usi VS Code, apri la cartella con la Remote-WSL extension per un'esperienza integrata con l'ambiente Linux.

## Struttura delle cartelle

```
hermes_hft/
├── config/
│   ├── trading_params.yaml     # Parametri di trading di default
│   └── models/                 # Modelli XGBoost serializzati (champion.pkl, challenger.pkl)
├── dashboard/
│   ├── app.py                  # Entry point Streamlit (navigazione)
│   ├── app_pages/               # Le 4 pagine (home, configuration, control, logs)
│   ├── utils/                  # Client Redis sincrono, process manager, formatting, ohlc
│   ├── pids/                   # PID file dei processi avviati dalla dashboard
│   └── .streamlit/config.toml  # Bind su localhost
├── data/
│   ├── historical/              # Storico OHLC 1h in Parquet, per simbolo
│   ├── backup/                  # Copie di backup dello storico
│   ├── live_ohlc/                # Candele live 1m scritte da OHLCAggregator
│   ├── trades_history.csv       # Storico dei trade chiusi (scritto dall'Engine)
│   └── sentiment_history.csv    # Storico degli score di sentiment
├── data_engine/
│   └── news_fetcher.py         # Recupero news RSS per il servizio Sentiment
├── docs/                        # Questa documentazione
├── logs/                        # Log applicativi, con rotazione giornaliera
├── src/
│   ├── core/                    # Modelli dati Pydantic (Position, Signal, Config)
│   ├── engine/                  # TradingEngine (main.py)
│   ├── inference/                # MLInference + FeatureEngine
│   ├── sentiment/                # OllamaSentiment
│   ├── shared/                   # redis_client, notifier, json_encoder, ohlc_aggregator
│   ├── exit_model/                # ATRExitModel (SL/TP dinamici)
│   ├── volume_pattern/            # VolumePatternAnalyzer (conferma segnali)
│   ├── training/                  # feature_engine + trainer per (ri)addestrare i modelli
│   └── data_collector.py         # Raccolta dati storici
├── tests/                        # Test pytest
├── start.sh                      # Script di avvio dei tre processi
├── optimize_models.py             # Ottimizzazione/valutazione modelli
└── requirements.txt
```

## Test

I test usano **pytest** e si trovano in `tests/`. Esecuzione:

```bash
source venv/bin/activate
pytest              # esegue tutta la suite
pytest -v           # output verboso
pytest tests/test_atr_exit.py   # un solo file
```

Struttura attuale:

- `tests/test_atr_exit.py`: verifica che `ATRExitModel.calculate_exit_levels` produca sempre SL/TP coerenti con il lato della posizione (SL sotto e TP sopra il prezzo per un long, il contrario per uno short), sia nel caso fallback sia nel caso basato su ATR reale.
- `tests/test_engine_open_position.py`: verifica che `TradingEngine._open_position` apra posizioni long/short corrette a partire da un `Signal`, usando un `FakeRedis` per isolare il test da una connessione Redis reale.

**Come scrivere un nuovo test**:

1. Se il test esercita `TradingEngine`, segui il pattern di `test_engine_open_position.py`: istanzia `TradingEngine()`, sostituisci `engine.redis` con un doppio minimale (evita di richiedere un Redis reale), e usa `unittest.mock.patch` per silenziare `notifier` se il codice sotto test lo chiama.
2. Per componenti puri come `ATRExitModel` o `VolumePatternAnalyzer`, non serve mockare nulla: sono classi senza dipendenze esterne.
3. Aggiungi test soprattutto per la logica critica di trading (calcolo SL/TP, condizioni di apertura/chiusura, filtri sui segnali) — è la superficie con l'impatto più alto in caso di regressione.
4. Nomina i test in modo descrittivo del comportamento atteso (es. `test_short_signal_opens_short_with_tp_below_and_sl_above_entry`), non dell'implementazione.

## Convenzioni di codice

- **Stile**: PEP8, indentazione a 4 spazi, nessuna riga superiore a ~120 caratteri.
- **Logging**: usa sempre `loguru` (`from loguru import logger`), mai `print`. I livelli usati nel progetto: `logger.debug` per dettagli ad alta frequenza (es. singoli segnali `hold`), `logger.info` per eventi di business (apertura/chiusura posizione, cambi di configurazione), `logger.warning` per condizioni anomale ma non fatali, `logger.error` per eccezioni gestite.
- **Docstring**: non sistematiche in questo progetto — dove presenti sono brevi commenti sullo scopo del modulo/classe in cima al file. Segui lo stile esistente: spiega il *perché*/il contesto non ovvio, non ripetere ciò che il nome della funzione già dice.
- **Modelli dati**: qualunque struttura scambiata su Redis o esposta a un form della dashboard deve passare da un modello Pydantic (`src/core/models.py`), così la validazione è centralizzata.
- **Async**: Engine, Inference e Sentiment sono interamente `asyncio`; evita chiamate bloccanti (I/O sincrono, sleep bloccanti) nei task asincroni — usa `aiohttp`/`asyncio.sleep`.

## Gestione del versionamento

- Branch principale: `main`. I branch di lavoro seguono un prefisso descrittivo, es. `feature/dashboard-support`, `fix-critical-bugs`.
- Scrivi commit atomici e descrivi *perché* è stato fatto un cambiamento (i messaggi di commit del repository sono in italiano — segui lo stesso stile per coerenza).
- Prima di aprire una pull request verso `main`: esegui `pytest`, verifica che i tre processi si avviino senza errori (`./start.sh engine|inference|sentiment`) e che la dashboard si apra senza eccezioni (`streamlit run dashboard/app.py`).
- Le pull request dovrebbero descrivere l'impatto operativo del cambiamento (es. "cambia il calcolo di SL/TP, testato in paper trading per 24h") dato che si tratta di codice che muove denaro reale.

## Debug

- **Log applicativi**: `logs/{trading|inference|sentiment}_YYYY-MM-DD.log` (rotazione giornaliera, retention 30 giorni) oppure via dashboard → pagina Log con filtro per livello.
- **Log stdout dei processi avviati dalla dashboard**: `logs/{engine|inference|sentiment}_stdout.log`.
- **Redis CLI**: strumento principale per ispezionare lo stato condiviso in tempo reale.
  ```bash
  redis-cli get positions              # posizioni aperte (JSON)
  redis-cli get trading_config          # configurazione attiva
  redis-cli get latest_price_BTCUSDT    # ultimo prezzo BTC
  redis-cli get heartbeat_engine        # ultimo heartbeat dell'Engine
  redis-cli monitor                     # tutti i comandi Redis in tempo reale
  redis-cli publish config_updated 1    # forza il reload della config sull'Engine
  ```
- **Processi**: `pgrep -af "python -m src"` per vedere quali dei tre moduli sono effettivamente in esecuzione, indipendentemente da come sono stati avviati (manualmente o dalla dashboard).
- **Test manuali di script isolati**: `test_inference.py` e `verify_overnight.py` nella root sono script ad hoc utili per verificare il comportamento di Inference/del sistema durante run prolungati, al di fuori della suite pytest.
