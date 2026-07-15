# API — Interfacce Redis

Hermes HFT non espone una API HTTP: l'unica interfaccia tra i processi è **Redis**, usato sia come key-value store per lo stato condiviso sia come message broker (pub/sub) per gli eventi. Questo documento descrive il formato esatto di ogni chiave e messaggio. Per la vista d'insieme del flusso dati vedi [docs/ARCHITECTURE.md](ARCHITECTURE.md#dettaglio-redis).

Tutte le connessioni sono su `localhost:6379`, `db 0`. I processi backend (Engine, Inference, Sentiment) usano il client asincrono `src/shared/redis_client.py`; la dashboard usa un client sincrono equivalente (`dashboard/utils/redis_client.py`).

## Convenzioni

- I valori strutturati (dict/list) sono serializzati in JSON con `to_json` (`src/shared/json_encoder.py`), che gestisce `datetime`/`date` come stringhe ISO 8601.
- I valori scalari (prezzi, score) sono salvati come stringhe (Redis non ha un tipo numerico nativo); vanno castati a `float`/`int` lato consumer.
- Nessuna delle chiavi ha un TTL impostato esplicitamente: restano valide finché non vengono sovrascritte o Redis viene svuotato/riavviato senza persistenza.

## Chiavi (key-value store)

### `positions`

- **Scritta da**: Engine (`_save_positions_to_redis`, ad ogni apertura/chiusura/aggiornamento trailing stop)
- **Letta da**: Engine (al riavvio), Dashboard
- **Formato**: JSON, dizionario `{SYMBOL: Position}` — contiene solo le posizioni con `is_open: true`.

```json
{
  "BTCUSDT": {
    "symbol": "BTCUSDT",
    "side": "long",
    "entry_price": 62450.5,
    "quantity": 0.024,
    "leverage": 3,
    "stop_loss": 61200.0,
    "take_profit": 64100.0,
    "trailing_stop": 61513.75,
    "entry_time": "2026-07-14T09:32:11.123456",
    "pnl": 18.42,
    "is_open": true
  }
}
```

### `trading_config`

- **Scritta da**: Engine (al primo avvio, se assente su Redis, la popola dal YAML), Dashboard (pagine Configurazione e Controllo)
- **Letta da**: Engine (all'avvio e ad ogni `config_updated`), Dashboard
- **Formato**: JSON, corrisponde al modello Pydantic `Config` (vedi [docs/ARCHITECTURE.md](ARCHITECTURE.md#configurazione-e-parametri) per il significato di ogni campo).

```json
{
  "leverage": 3,
  "stop_loss_pct": 0.05,
  "take_profit_pct": 0.04,
  "max_position_size_usdt": 50.0,
  "trailing_stop_pct": 0.015,
  "max_exposure": 0.5,
  "min_volatility_threshold": 0.001,
  "max_volatility_threshold": 0.02,
  "volatility_adjustment": true,
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "timeframe": "1h",
  "ml_confidence_threshold": 0.55,
  "sentiment_weight": 0.3,
  "sentiment_asset_enabled": true,
  "reverse_trading_enabled": true,
  "pattern_confirmation_enabled": true,
  "dynamic_exit_enabled": true
}
```

### `latest_price_{SYMBOL}`

- **Scritta da**: Engine, ad ogni tick WebSocket (`latest_price_BTCUSDT`, `latest_price_ETHUSDT`, `latest_price_SOLUSDT`)
- **Letta da**: Dashboard (Home, per prezzo corrente e overlay sul grafico candele)
- **Formato**: stringa numerica, es. `"62450.5"`.

### `heartbeat_engine` / `heartbeat_inference` / `heartbeat_sentiment`

- **Scritta da**: rispettivamente Engine (ogni ~5s, in `_position_monitor`), Inference (ogni ciclo, ~5s, in `_inference_loop`), Sentiment (ogni 15s, in `_heartbeat_loop`)
- **Letta da**: Dashboard, per determinare lo stato "ok/stale/down" di ogni servizio
- **Formato**: stringa, timestamp ISO 8601 UTC, es. `"2026-07-14T10:15:03.884210+00:00"`.

### `sentiment_score`

- **Scritta da**: Sentiment, ad ogni ciclo di analisi (~ogni 5 minuti)
- **Letta da**: Dashboard
- **Formato**: stringa numerica in [-1, 1], es. `"0.32"`.

### `sentiment_btc` / `sentiment_eth` / `sentiment_sol`

- **Scritta da**: Sentiment, ad ogni ciclo
- **Letta da**: Dashboard (`get_sentiment_by_asset`) — è la **fonte persistita** dello score per asset, dato che `sentiment_asset` (canale) non ha una chiave persistente corrispondente.
- **Formato**: stringa numerica in [-1, 1] per singolo asset.

## Canali (pub/sub)

### `ml_signals`

- **Pubblicato da**: Inference, ogni ~5 secondi per ciascun simbolo senza posizione aperta e con predizione `buy`/`sell` (i segnali `hold` non vengono pubblicati)
- **Consumato da**: Engine (`_redis_listener` → `_on_signal`)
- **Payload**: JSON, corrisponde al modello `Signal`.

```json
{
  "symbol": "ETHUSDT",
  "action": "buy",
  "confidence": 0.734,
  "timestamp": "2026-07-14T10:15:05.001Z",
  "source": "ml"
}
```

`action` ∈ `{"buy", "sell", "hold", "close"}` (nella pratica Inference emette solo `buy`/`sell`; `close` è supportato dal modello ma non generato dal codice attuale). `confidence` è la probabilità restituita da `model.predict_proba` (0-1).

### `sentiment_update`

- **Pubblicato da**: Sentiment, ad ogni ciclo (~5 minuti)
- **Consumato da**: Engine (aggiorna `self.sentiment_score`)
- **Payload**: stringa numerica (non JSON), es. `"0.18"`.

### `sentiment_asset`

- **Pubblicato da**: Sentiment, ad ogni ciclo
- **Consumato da**: Engine (aggiorna `self.sentiment_by_asset`, usato per pesare la confidenza dei segnali ML)
- **Payload**: JSON.

```json
{
  "BTC": 0.21,
  "ETH": -0.05,
  "SOL": 0.40,
  "aggregate": 0.19
}
```

### `config_updated`

- **Pubblicato da**: Dashboard (pagine Configurazione e Controllo), dopo aver scritto `trading_config`
- **Consumato da**: Engine — al ricevimento, ricarica interamente la configurazione da Redis (`_load_config_from_redis`)
- **Payload**: valore libero, ignorato dal consumer (funge da semplice trigger). La dashboard pubblica `"1"`.

### `engine_commands`

- **Pubblicato da**: Dashboard (pulsante "Reset posizioni (emergenza)" nella pagina Controllo)
- **Consumato da**: Engine
- **Payload**: JSON con un campo `action`. **Unico comando attualmente supportato**: `close_all`.

```json
{
  "action": "close_all",
  "reason": "EMERGENCY_RESET"
}
```

Alla ricezione, l'Engine chiude tutte le posizioni aperte tramite il normale flusso `_close_position` (calcolo PnL, notifiche, registrazione su `data/trades_history.csv`), usando `reason` come motivazione registrata nello storico.

## Estendere l'interfaccia

Se aggiungi un nuovo canale o comando (es. un nuovo tipo di `engine_commands`), segui questi accorgimenti già in uso nel codice:

- Valida sempre i payload strutturati con un modello Pydantic quando possibile (vedi `src/core/models.py`), invece di leggere i campi da un dict non tipato.
- Avvolgi il parsing in `try/except` con `logger.error` in caso di fallimento (pattern consistente in `_redis_listener`): un messaggio malformato su un canale non deve far crashare il listener.
- Documenta qui il nuovo canale/chiave e aggiorna la tabella corrispondente in [docs/ARCHITECTURE.md](ARCHITECTURE.md#dettaglio-redis).
