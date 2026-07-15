# Deployment

> **Stato attuale**: il repository offre **due bersagli di deployment**. (1) **Docker**: `Dockerfile` + `docker-compose.yml` per l'intero stack (redis, ollama, servizi, dashboard, watchdog) — vedi [DOCKER.md](DOCKER.md), la via consigliata per server/VPS. (2) **Nativo**: unit systemd **utente** in `deploy/systemd/` con installer `deploy/install_systemd.sh` — niente sudo per start/stop quotidiani, riavvio automatico su crash, `dashboard/utils/process_manager.py` le usa automaticamente quando installate (fallback su `start.sh`); è la via usata per sviluppo e test su WSL2 (`systemd=true` in `[boot]` di `/etc/wsl.conf` + `wsl --shutdown`; avvio al boot senza login: `sudo loginctl enable-linger <utente>`). **Mai i due stack accesi insieme sulla stessa macchina** (doppio engine = doppio paper trading sugli stessi dati). Le raccomandazioni sotto (backup, aggiornamenti, sicurezza) valgono in entrambi i casi.

## Indice

- [Requisiti di produzione](#requisiti-di-produzione)
- [Configurazione per produzione](#configurazione-per-produzione)
- [Backup e ripristino](#backup-e-ripristino)
- [Aggiornamenti](#aggiornamenti)
- [Sicurezza](#sicurezza)

## Requisiti di produzione

- **Server**: una VM/host Linux always-on (es. VPS) con almeno 2 vCPU e 2 GB di RAM è sufficiente per i 3 processi + Redis; l'Engine viene pinnato su 2 CPU (`taskset -c 0,1` in `start.sh`), quindi almeno 2 core dedicati sono raccomandati per evitare contesa con Redis/dashboard.
- **Connettività**: connessione stabile e a bassa latenza verso `fstream.binance.com` (WebSocket) e `fapi.binance.com` (fallback REST) — la qualità della connessione impatta direttamente sulla tempestività di apertura/chiusura posizioni.
- **Redis**: istanza dedicata (anche locale sullo stesso host va bene per un singolo bot) con persistenza abilitata (vedi [Backup e ripristino](#backup-e-ripristino)).
- **Ollama**: se il servizio Sentiment è in uso, serve una macchina con risorse sufficienti per servire il modello LLM locale in tempi ragionevoli (la chiamata ha timeout di 45s in `src/sentiment/ollama_client.py`); su hardware modesto, considera un modello più piccolo o l'esecuzione di Ollama su una macchina separata raggiungibile via `OLLAMA_HOST`.
- **Dipendenze**: le stesse di sviluppo ([requirements.txt](../requirements.txt)), installate in un virtualenv dedicato, non nel Python di sistema.

## Configurazione per produzione

- **Avvio persistente dei processi**: `start.sh` è pensato per un terminale interattivo. In produzione, avvolgilo con un supervisore di processo che lo riavvii in caso di crash. Esempio con `systemd` (unit non incluso nel repo — da creare):

  ```ini
  # /etc/systemd/system/hermes-engine.service
  [Unit]
  Description=Hermes HFT - Engine
  After=network-online.target redis-server.service

  [Service]
  Type=simple
  WorkingDirectory=/home/<utente>/hermes_hft
  ExecStart=/home/<utente>/hermes_hft/start.sh engine
  Restart=on-failure
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  ```

  Replica per `hermes-inference.service` e `hermes-sentiment.service` (cambiando solo l'argomento di `ExecStart`). In alternativa, `tmux`/`screen` con riavvio manuale sono accettabili per un setup a singolo operatore, ma perdono il riavvio automatico su crash.
- **Redis**: abilita la persistenza (`appendonly yes` in `redis.conf`, oppure snapshot RDB regolari) — è la fonte di verità per posizioni aperte e configurazione: un riavvio senza persistenza fa perdere lo stato delle posizioni aperte (l'Engine le ricaricherebbe come "nessuna posizione", pur essendo ancora aperte sull'exchange se in futuro si collegasse un execution reale).
- **Log**: la rotazione giornaliera con retention 30 giorni è già configurata in ogni processo (`logger.add(..., rotation="1 day", retention="30 days")`). Per produzione, valuta di centralizzare i log (es. spedendoli a un servizio esterno) se operi più istanze o vuoi alerting sugli errori senza tail manuale.
- **Notifiche**: configura `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` (e opzionalmente l'email) in `.env` così da ricevere notifiche in tempo reale su apertura/chiusura posizioni ed errori critici, senza dover monitorare i log attivamente.
- **Monitoraggio**: gli heartbeat su Redis (`heartbeat_engine`, `heartbeat_inference`, `heartbeat_sentiment`) sono pensati per essere osservati dalla dashboard, ma possono anche essere controllati da un cron/script esterno che avvisa se un heartbeat è più vecchio della soglia (20s Engine/Inference, 60s Sentiment) — utile se la dashboard non è tenuta aperta costantemente.
- **Dashboard in produzione**: resta volutamente vincolata a `localhost` (vedi [docs/DASHBOARD.md](DASHBOARD.md)); per accedervi da remoto in sicurezza usa un tunnel SSH (`ssh -L 8501:localhost:8501 utente@server`) invece di esporla direttamente — vedi [Sicurezza](#sicurezza).

## Backup e ripristino

**Cosa fare backup**:

- **Redis**: `positions` e `trading_config` sono lo stato critico. Se la persistenza RDB è abilitata, esegui backup periodici del file `dump.rdb` (es. cron con `cp`/rsync verso storage esterno). Per un backup puntuale manuale:
  ```bash
  redis-cli save            # forza uno snapshot sincrono
  cp /var/lib/redis/dump.rdb /percorso/di/backup/dump-$(date +%F).rdb
  ```
- **Configurazione**: `config/trading_params.yaml` e `.env` (quest'ultimo contiene segreti — proteggi il backup di conseguenza).
- **Dati storici**: `data/trades_history.csv`, `data/sentiment_history.csv`, `data/historical/*.parquet` — utili per analisi di performance e per non perdere lo storico dei trade.
- **Modelli**: `config/models/champion.pkl` (e `challenger.pkl` se in uso) — se non versionati in git, tienine una copia separata.

**Ripristino**:

1. Ferma i tre processi (`./start.sh` non ha un comando di stop centralizzato: usa la dashboard o `pkill -f "python -m src.<modulo>"`).
2. Ripristina `dump.rdb` nella directory dati di Redis e riavvia il servizio Redis.
3. Verifica con `redis-cli get positions` e `redis-cli get trading_config` che lo stato sia quello atteso.
4. Ripristina eventuali file `data/*.csv`/`*.parquet` se necessario.
5. Riavvia i tre processi.

## Aggiornamenti

Non essendoci downtime "zero" nativo (i tre processi sono singole istanze, non ridondate), l'aggiornamento più sicuro è sequenziale:

1. **Fai il pull del nuovo codice** su un checkout separato o dopo aver verificato che non ci siano modifiche locali non salvate (`git status`).
2. **Aggiorna le dipendenze** se `requirements.txt` è cambiato: `pip install -r requirements.txt` nel virtualenv.
3. **Aggiorna un processo alla volta**, iniziando da quelli meno critici per lo stato:
   - **Sentiment**: può essere fermato/riavviato senza impatto immediato (il sentiment resta quello dell'ultimo ciclo su Redis).
   - **Inference**: fermalo, aggiorna, riavvialo — l'Engine smette temporaneamente di ricevere nuovi segnali ma le posizioni aperte restano gestite (SL/TP/trailing continuano a funzionare, dato che dipendono dal WebSocket dell'Engine, non da Inference).
   - **Engine**: è il processo più delicato perché tiene lo stato delle posizioni aperte in memoria (persistito su Redis ad ogni variazione). Prima di riavviarlo, verifica che `positions` su Redis sia aggiornato (`redis-cli get positions`); al riavvio l'Engine lo ricarica automaticamente da `_load_positions_from_redis`, quindi un riavvio breve è sicuro, ma **evita di riavviarlo mentre una posizione è vicina a SL/TP** se possibile, dato che durante il riavvio il monitoraggio del prezzo è sospeso.
4. **`start.sh` termina automaticamente le istanze residue** dello stesso modulo prima di avviarne una nuova, quindi un semplice `./start.sh <servizio>` (o il pulsante "Avvia" in dashboard, che rifiuta l'azione se già in esecuzione — usa prima "Ferma") applica l'aggiornamento.
5. **La configurazione** (`config/trading_params.yaml`) non richiede repush manuale se già allineata su Redis: solo il codice va aggiornato.

## Sicurezza

- **Non esporre la dashboard su `0.0.0.0`**: resta su `localhost` (già configurato) e accedi da remoto solo via tunnel SSH o VPN. La dashboard permette di modificare parametri di trading e chiudere posizioni senza autenticazione integrata.
- **Non esporre Redis in rete**: mantienilo su `localhost` (bind di default) o, se serve accesso remoto, proteggilo con `requirepass` e/o regole firewall che limitino le sorgenti consentite. Redis di per sé non ha autenticazione forte né TLS out-of-the-box.
- **Proteggi `.env`**: contiene token Telegram, credenziali email; non committarlo mai (già escluso da `.gitignore`) e limita i permessi del file (`chmod 600 .env`).
- **Firewall**: apri solo le porte strettamente necessarie (nessuna, se dashboard e Redis restano su `localhost` e l'accesso remoto passa da SSH); chiudi 6379 (Redis) e 8501 (Streamlit) verso l'esterno.
- **Aggiornamenti di sistema**: mantieni aggiornati OS, Python e le dipendenze (`pip list --outdated`) per le patch di sicurezza, specialmente per librerie di rete (`aiohttp`, `websockets`, `requests`).
- **Segreti nei log**: verifica periodicamente che nessun log stampi token o credenziali (il codice attuale non lo fa, ma vale la pena controllare dopo ogni modifica a `notifier.py` o simili).
- **Least privilege**: esegui i processi con un utente Linux dedicato, non root, e con permessi limitati alla sola directory del progetto.
