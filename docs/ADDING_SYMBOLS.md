# Aggiungere un nuovo simbolo

Guida passo-passo per aggiungere una nuova coppia di trading a Hermes HFT dopo
il refactoring multi-simbolo. Il percorso di trading core (Engine, Inference,
dashboard) è genuinamente dinamico: aggiungere un simbolo richiede l'edit di
un file di configurazione più il retraining del modello, **non** modifiche al
codice.

**Fuori da questa guida** (non ancora generalizzato, vedi nota in fondo):
la pipeline sentiment (`src/sentiment/ollama_client.py` +
`data_engine/news_fetcher.py`) resta cablata su BTC/ETH/SOL — un nuovo
simbolo funziona regolarmente nel trading e in dashboard, ma senza sentiment
per-asset dedicato (fallback automatico al sentiment aggregato).

## 1. Verifica preliminare: liquidità e volatilità

Prima di aggiungere un simbolo, vale la pena verificare che sia adatto alla
strategia — non tutte le coppie liquide generano occasioni di trading (una
volatilità troppo bassa produce zero operazioni, come osservato durante il
test overnight di questa sessione con i simboli originali).

Scarica lo storico e calcola correlazione/volatilità reali prima di decidere:

```python
from src.data_collector import DataCollector
import pandas as pd
import numpy as np

collector = DataCollector()
df = collector.download_historical("NUOVO/USDT", timeframe="1h", days=365)
collector.save_to_parquet(df, "NUOVOUSDT", timeframe="1h")

btc = pd.read_parquet("data/historical/BTCUSDT_1h.parquet")["close"].pct_change().dropna()
nuovo = df["close"].pct_change().dropna()
aligned = pd.DataFrame({"btc": btc, "nuovo": nuovo}).dropna()

print("Correlazione con BTC:", aligned["btc"].corr(aligned["nuovo"]))
print("Volatilità annualizzata %:", nuovo.std() * np.sqrt(24 * 365) * 100)
```

Cerca un compromesso tra bassa correlazione con BTC (diversificazione) e
volatilità sufficiente a generare segnali (indicativamente, i simboli attuali
vanno dal 27.7% di TRXUSDT al 77.9% di ADAUSDT annualizzato).

## 2. Aggiungi il simbolo a `config/trading_params.yaml`

```yaml
symbols:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - NUOVOUSDT   # <- aggiunto qui
```

## 3. Pubblica la configurazione su Redis

Se Engine/Inference sono già in esecuzione, la chiave Redis `trading_config`
ha priorità sul file YAML — un edit del solo YAML non basta. Due modi:

- **Dashboard** (consigliato): pagina *Configurazione*, aggiungi il simbolo al
  campo "Simboli", premi "Salva e applica". Scrive su Redis, pubblica
  `config_updated` e riallinea anche il file YAML.
- **Manuale**, se preferisci non passare dalla dashboard:
  ```python
  import json, redis
  from src.core.models import Config

  client = redis.Redis(host="localhost", port=6379, decode_responses=True)
  current = json.loads(client.get("trading_config"))
  current["symbols"].append("NUOVOUSDT")
  validated = Config(**current)  # valida prima di scrivere
  client.set("trading_config", validated.model_dump_json())
  client.publish("config_updated", "1")
  ```

## 4. Hot-reload o riavvio

- **Engine**: reagisce a `config_updated` senza riavvio — ricostruisce
  automaticamente `exit_models`/`pattern_models` per il nuovo simbolo
  (`_apply_config`), preservando lo stato di quelli esistenti.
- **Inference**: stesso comportamento, ricostruisce `feature_engines` per il
  nuovo simbolo. La (ri)connessione WebSocket con lo stream del nuovo simbolo
  avviene però solo alla prossima riconnessione — se serve immediato,
  riavvia il servizio dalla dashboard (pagina Controllo) o con `./start.sh inference`.
- **Sentiment**: non ha bisogno di modifiche per i simboli esistenti che già
  gestisce; per il nuovo simbolo non farà nulla di diverso da oggi (vedi nota
  in fondo).

## 5. Riaddestra il modello

Il modello ML non si aggiorna da solo: serve rilanciare il training perché il
nuovo simbolo contribuisca al dataset (Approccio A, modello unico pooled).

```bash
python train_all_models.py
```

Lo script legge i simboli direttamente da `config/trading_params.yaml` (non
serve modificarlo), scarica lo storico mancante, calcola le feature **per
simbolo** prima di concatenare (necessario: RSI/SMA/ATR/MACD sono calcoli su
finestre temporali, mescolare simboli prima del calcolo genererebbe valori
falsi ai confini), fa uno split train/validation temporale per simbolo prima
di unire (altrimenti la validazione finirebbe sbilanciata sull'ultimo simbolo
della lista), e promuove il nuovo modello solo se batte realmente quello
attuale sulla validazione combinata. Vedi `docs/TRAINING.md` per i dettagli.

## 6. (Opzionale) Ottimizza i moltiplicatori ATR

`src/engine/main.py` ha due dizionari (`DEFAULT_SL_MULTIPLIERS`,
`DEFAULT_TP_MULTIPLIERS`) che permettono SL/TP dinamico su misura per
simbolo, in base alla sua volatilità tipica. Un simbolo non presente usa il
fallback 5.0/5.5. Se il test overnight mostra che il fallback è troppo
largo/stretto per il nuovo simbolo, aggiungilo esplicitamente al dizionario.

## 7. Verifica

- **Log Engine/Inference**: cerca la riga di avvio con l'elenco simboli
  aggiornato, e conferma che non ci siano errori WebSocket per il nuovo
  stream.
- **Dashboard → Home**: dovrebbe comparire una nuova tab candlestick per il
  simbolo (si aggiorna da sola entro 15s dal caricamento della pagina).
- **`data/live_ohlc/NUOVOUSDT.csv`**: dovrebbe iniziare a popolarsi entro un
  minuto dal primo tick ricevuto.
- **Log Inference**: cerca "Segnale ML" per il nuovo simbolo per confermare
  che il modello sta producendo predizioni per esso.

## Nota: pipeline sentiment non generalizzata

`src/sentiment/ollama_client.py` e `data_engine/news_fetcher.py` restano
cablati su BTC/ETH/SOL (prompt LLM, parsing della risposta, feed RSS,
chiavi Redis `sentiment_btc/eth/sol`). Un nuovo simbolo funziona comunque:
`_on_signal` nell'Engine usa `sentiment_by_asset.get(symbol, sentiment_score)`,
quindi ricade automaticamente sul sentiment aggregato generale invece di un
valore per-asset dedicato — nessun errore, solo un segnale meno specifico.
Generalizzare questa parte richiede curare feed RSS reali per ogni nuova
moneta ed è tracciato come lavoro separato.
