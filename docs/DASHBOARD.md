# Dashboard

Guida pratica alla dashboard web di Hermes HFT: cosa mostra ogni pagina, come modificare la configurazione, come avviare/fermare i processi e come reagire in caso di emergenza.

## Indice

- [Panoramica](#panoramica)
- [Simboli dinamici](#simboli-dinamici)
- [Pagine](#pagine)
- [Come usarla](#come-usarla)
- [Gestione dei processi](#gestione-dei-processi)
- [Comandi di emergenza](#comandi-di-emergenza)
- [Troubleshooting](#troubleshooting)

## Panoramica

La dashboard (`dashboard/app.py`) è un'app **Streamlit multipagina** che:

- Legge lo stato del sistema da Redis (posizioni, prezzi, heartbeat, sentiment, configurazione) tramite un client sincrono dedicato (`dashboard/utils/redis_client.py`) — separato dal client asincrono usato dai tre processi di backend.
- Legge le candele OHLC direttamente da file: storico orario da `data/historical/*.parquet` e candele intraday live da `data/live_ohlc/*.csv` (scritte da `src/shared/ohlc_aggregator.py`).
- Avvia/ferma Engine, Inference e Sentiment come sottoprocessi, riusando lo script `start.sh` (nessuna logica di avvio duplicata).
- **Non è esposta in rete**: `dashboard/.streamlit/config.toml` la vincola a `localhost`, perché permette di modificare la configurazione di trading e di chiudere posizioni.

Per avviarla:

```bash
streamlit run dashboard/app.py
```

poi apri `http://localhost:8501`.

## Simboli dinamici

La dashboard non ha più una lista di simboli fissa nel codice: la Home legge
`get_trading_config()["symbols"]` a ogni refresh del grafico prezzi (ogni 15s,
via `st.fragment`), quindi un simbolo aggiunto a `trading_params.yaml`/Redis
ottiene automaticamente una tab candlestick senza toccare il codice — vedi
`docs/ADDING_SYMBOLS.md` per la procedura completa. Il pannello sentiment per
asset (`dashboard/utils/redis_client.py::get_sentiment_by_asset`) itera allo
stesso modo sui simboli configurati; un simbolo senza sentiment dedicato
(oggi solo BTC/ETH/SOL ce l'hanno) semplicemente non compare in quella
tabella, senza errori.

## Pagine

### Dashboard (Home) — `app_pages/home.py`

La pagina di monitoraggio principale, aggiornata automaticamente a intervalli diversi per sezione (via `st.fragment(run_every=...)`):

- **Stato processi** (ogni 5s): un riquadro per Engine, Inference, Sentiment con icona di stato (🟢 ok / 🟡 stale / 🔴 down) e PID. Lo stato incrocia il processo effettivamente in esecuzione (via `psutil`) con la freschezza dell'heartbeat su Redis (soglia 20s per Engine/Inference, 60s per Sentiment).
- **KPI** (ogni 5s): capitale iniziale, capitale attuale con delta e mini-grafico dell'equity curve, drawdown massimo — tutti calcolati da `data/trades_history.csv`.
- **Posizioni aperte** (ogni 5s): tabella con entry, prezzo corrente, SL, TP, PnL corrente e PnL stimato a SL/TP, più il PnL totale.
- **Ultime operazioni**: le 10 righe più recenti di `data/trades_history.csv`.
- **Prezzi**: grafici a candele (uno per simbolo, in tab) che uniscono storico orario e candele live a 1 minuto, con una linea tratteggiata sul prezzo live corrente (refresh ogni 15s).
- **Sentiment e segnali**: score di sentiment aggregato e per asset, più gli ultimi 20 segnali ML estratti in tempo reale dal log di Inference.

### Configurazione — `app_pages/configuration.py`

Form per modificare tutti i parametri di `Config` (leva, SL/TP, trailing stop, esposizione massima, soglie di volatilità, soglia di confidenza ML, peso del sentiment, simboli tradati, timeframe, e i tre toggle di funzionalità). Al salvataggio:

1. I valori vengono validati tramite il modello Pydantic `Config` (errori di validazione mostrati a schermo, nulla viene salvato se non validi).
2. La configurazione viene scritta sulla chiave Redis `trading_config` e pubblicata sul canale `config_updated`.
3. Viene anche riscritta su `config/trading_params.yaml`, così il file resta sincronizzato con Redis (se la scrittura su disco fallisce, viene mostrato un warning ma il salvataggio su Redis resta valido).

L'Engine ricarica la configurazione automaticamente non appena riceve `config_updated` — **non serve riavviarlo**.

### Controllo — `app_pages/control.py`

- **Processi**: pulsanti **Avvia**/**Ferma** per ciascun servizio, con conferma esplicita prima dell'arresto. Lo stato mostrato è lo stesso della Home (processo + heartbeat).
- **Funzionalità**: tre toggle rapidi per `reverse_trading_enabled`, `pattern_confirmation_enabled`, `dynamic_exit_enabled`, che salvano immediatamente su Redis (senza passare dal form completo di Configurazione).
- **Azioni di emergenza**: pulsante "Reset posizioni (emergenza)" — vedi sotto.

### Log — `app_pages/logs.py`

- Selettore del servizio (Engine/Inference/Sentiment) e filtro per livello (INFO/WARNING/ERROR/DEBUG).
- Selettore del file di log (uno per giorno, pattern `{prefix}_YYYY-MM-DD.log`); il file più recente viene mostrato con auto-refresh ogni 2 secondi, i file più vecchi sono statici.
- Pulsante per scaricare il file di log completo.

## Come usarla

**Monitorare il bot**: apri la pagina Home. Verifica che tutti e tre i pallini di stato siano verdi, controlla PnL ed eventuali posizioni aperte, e usa il tab "Segnali ML" per capire se Inference sta generando segnali.

**Modificare la configurazione**: vai su Configurazione, cambia i valori necessari (es. `leverage`, `stop_loss_pct`), premi "Salva e applica". La modifica è live: l'Engine la applica al giro successivo senza downtime.

**Controllare i processi**: vai su Controllo per avviare un servizio fermo o fermarne uno in esecuzione. I toggle di funzionalità qui sono una scorciatoia rispetto al form di Configurazione.

**Consultare i log**: vai su Log, seleziona il servizio e (opzionalmente) filtra per livello per isolare errori o warning.

## Gestione dei processi

`dashboard/utils/process_manager.py` gestisce l'intero ciclo di vita dei tre processi:

- **Avvio**: lancia `./start.sh <servizio>` come sottoprocesso indipendente (`start_new_session=True`), reindirizzando stdout/stderr su `logs/{servizio}_stdout.log`. Il PID viene salvato in `dashboard/pids/{servizio}.pid`. Se il processo risulta già in esecuzione (rilevato via `psutil` cercando la riga di comando `python -m src.<modulo>`), l'avvio viene rifiutato.
- **Arresto**: invia `SIGTERM` a tutti i PID trovati per quel modulo, attende fino a 8 secondi che terminino, e se necessario forza con `SIGKILL`. Il file PID viene rimosso in ogni caso.
- **Stato**: determinato cercando processi live il cui `cmdline` contiene il nome del modulo — non si basa solo sul file PID, quindi resta corretto anche se il processo è stato avviato manualmente fuori dalla dashboard.

Poiché riusa `start.sh`, l'avvio dalla dashboard beneficia automaticamente della stessa logica di pulizia delle istanze residue presente nello script.

## Comandi di emergenza

Il pulsante **"Reset posizioni (emergenza)"** (pagina Controllo) **non uccide l'Engine** e **non tocca Redis direttamente**: pubblica un comando JSON `{"action": "close_all", "reason": "EMERGENCY_RESET"}` sul canale `engine_commands`. È l'Engine, ancora in esecuzione, a chiudere ogni posizione aperta passando dal normale flusso `_close_position` — quindi con calcolo PnL corretto, notifiche Telegram/email coerenti e registrazione su `data/trades_history.csv`.

**Quando usarlo**: comportamento di mercato anomalo, necessità di uscire immediatamente da tutte le posizioni, o sospetto di un bug nella logica di apertura/chiusura che si vuole arginare subito.

**Come usarlo in sicurezza**:

1. Il pulsante richiede una conferma esplicita (secondo click su "Conferma chiusura di tutte le posizioni") — leggi il messaggio di avviso prima di confermare.
2. Assicurati che l'Engine sia effettivamente in esecuzione (pallino verde): se è down, il comando resta pubblicato ma nessuno lo consuma finché l'Engine non riparte.
3. Dopo l'invio, verifica in Home che la lista "Posizioni aperte" sia vuota e controlla il log dell'Engine per confermare l'esito.
4. Se l'obiettivo è anche fermare il trading (non solo chiudere le posizioni correnti), disattiva i segnali fermando Inference dalla pagina Controllo, oppure abbassa `ml_confidence_threshold`/disattiva i toggle di funzionalità da Configurazione.

## Troubleshooting

**"Connection refused" o dashboard che si blocca su ogni pagina che legge Redis**
Redis non è in esecuzione. Verifica con `redis-cli ping` (deve rispondere `PONG`) e avvialo con `sudo service redis-server start`.

**Un servizio resta "🔴 down" anche dopo averlo avviato dalla dashboard**
Controlla `logs/{servizio}_stdout.log` per errori di avvio (es. dipendenza mancante, modello `.pkl` assente per Inference, Ollama non raggiungibile per Sentiment). Il pallino resta rosso finché non viene rilevato un processo con quel `cmdline` attivo.

**Un servizio è "🟡 stale" (giallo) pur essendo in esecuzione**
Il processo è vivo ma non scrive più l'heartbeat da più della soglia (20s Engine/Inference, 60s Sentiment) — tipicamente un loop bloccato (es. `_websocket_loop` in riconnessione continua, o chiamata a Ollama che va in timeout). Controlla il log del servizio per l'ultima riga scritta.

**Il pulsante "Avvia" è disabilitato ma nessun processo risulta attivo altrove**
`process_manager.status()` ha trovato un processo con quel modulo nella `cmdline` (magari zombie o avviato manualmente). Verifica con `pgrep -f "python -m src.engine.main"` (adattando il modulo) e termina manualmente il processo residuo se necessario.

**Le candele non si aggiornano**
Le candele live dipendono da Inference (che scrive `data/live_ohlc/*.csv` tramite `OHLCAggregator`): se Inference è fermo o non riceve tick, il grafico resta fermo all'ultima candela disponibile.

**La configurazione salvata non sembra avere effetto**
Verifica che l'Engine sia in esecuzione e controlla nel suo log la riga `🔄 Configurazione aggiornata, ricarico...`, che conferma la ricezione del `config_updated`. Se l'Engine era down al momento del salvataggio, la nuova configurazione è comunque su Redis e verrà caricata al prossimo avvio.
