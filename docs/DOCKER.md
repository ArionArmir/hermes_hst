# Esecuzione con Docker

Come eseguire l'intero stack Hermes — infrastruttura (Redis, Ollama) e servizi applicativi — con Docker Compose, e in cosa differisce dall'avvio manuale. Artefatti di riferimento nella radice del repo: [Dockerfile](../Dockerfile), [docker-compose.yml](../docker-compose.yml), [.dockerignore](../.dockerignore). Per backup, sicurezza e raccomandazioni di produzione vedi [DEPLOYMENT.md](DEPLOYMENT.md).

## Indice

- [Quando usare Docker e quando l'avvio manuale](#quando-usare-docker-e-quando-lavvio-manuale)
- [Anatomia dello stack](#anatomia-dello-stack)
- [Rete e variabili d'ambiente](#rete-e-variabili-dambiente)
- [Cosa è condiviso con l'host](#cosa-è-condiviso-con-lhost)
- [Primo avvio](#primo-avvio)
- [Operazioni quotidiane](#operazioni-quotidiane)
- [Differenze rispetto all'avvio manuale](#differenze-rispetto-allavvio-manuale)
- [GPU per Ollama (opzionale)](#gpu-per-ollama-opzionale)

## Quando usare Docker e quando l'avvio manuale

| | Manuale (WSL/host) | Docker Compose |
|---|---|---|
| Avvio | un terminale per processo, `start.sh` | `docker compose up -d` |
| Redis / Ollama | servizi di sistema da gestire a parte | container con volumi dedicati |
| Riavvio dopo crash | watchdog `--restart` o a mano | `restart: unless-stopped` automatico |
| Avvio/arresto da dashboard | sì (pagina Control) | no — `docker compose start/stop` |
| Aggiornare il codice | riavviare il processo | `docker compose up -d --build` |
| Adatto a | sviluppo quotidiano, debug | esecuzione continuativa, VPS, onboarding |

> **⚠️ Mai entrambi insieme.** Non lanciare lo stack Docker mentre i processi girano ancora a mano sull'host: due engine fanno **doppio paper trading** e scrivono sugli stessi CSV e log (le directory `data/` e `logs/` sono condivise via bind mount). Prima di `docker compose up`, fermare i processi manuali.

## Anatomia dello stack

Tutti i servizi applicativi usano **la stessa immagine** (`hermes-app`, build dal [Dockerfile](../Dockerfile)): stesso codice e stesse dipendenze, cambia solo il `command` nel compose. L'immagine gira come utente non-root `hermes` con UID 1000, allineato all'utente host: i file scritti sui bind mount restano editabili dall'host senza sudo.

| Servizio | Immagine | Ruolo | Note |
|---|---|---|---|
| `redis` | `redis:7-alpine` | bus pub/sub + stato condiviso | AOF attivo, volume `redis-data`, healthcheck `redis-cli ping` |
| `ollama` | `ollama/ollama` | LLM locale per il sentiment | volume `ollama-data` per i modelli, healthcheck `ollama list` |
| `ollama-init` | `ollama/ollama` | one-shot: `ollama pull` del modello | al primo avvio scarica ~1 GB, poi è un no-op |
| `engine` | `hermes-app` | trading engine | `python -m src.engine.main` |
| `inference` | `hermes-app` | segnali ML | `python -m src.inference.main` |
| `sentiment` | `hermes-app` | analisi news | parte solo quando `ollama` è healthy |
| `dashboard` | `hermes-app` | Streamlit | porta pubblicata **solo su 127.0.0.1** dell'host |
| `watchdog` | `hermes-app` | allarmi heartbeat | gira in loop (60 s), solo notifica |

I servizi applicativi partono solo a Redis healthy (`depends_on` con `condition: service_healthy`) e hanno `restart: unless-stopped`: un crash viene rialzato da Docker, non dal watchdog.

## Rete e variabili d'ambiente

Dentro la rete compose i servizi si parlano per nome: i container applicativi ricevono `REDIS_HOST=redis` e `OLLAMA_HOST=http://ollama:11434`. Fuori da Docker le stesse variabili non sono impostate e il codice usa i default `localhost` (vedi `src/shared/redis_client.py`) — l'avvio manuale continua a funzionare identico, senza configurazione aggiuntiva.

`HERMES_IN_DOCKER=1` segnala alla dashboard che i processi sono container: la pagina Control mostra lo stato dagli heartbeat e rimanda a `docker compose` invece dei pulsanti Avvia/Ferma (il process manager basato su `start.sh` e PID non può gestire container).

Variabili opzionali (inline o in `.env`):

| Variabile | Default | Effetto |
|---|---|---|
| `DASHBOARD_PORT` | `8501` | porta host della dashboard (sempre e solo su 127.0.0.1) |
| `OLLAMA_MODEL` | `qwen2.5-coder:1.5b` | modello scaricato da `ollama-init` |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, … | — | notifiche del Notifier e del watchdog |

Il file `.env` nella radice, se esiste, è iniettato nei container (`env_file` con `required: false`) ma **non** viene copiato nell'immagine (è escluso da `.dockerignore`, insieme a dati, log, venv e modelli).

## Cosa è condiviso con l'host

Tre directory sono bind mount, identiche dentro e fuori i container:

| Percorso | Contenuto | Perché è un bind mount |
|---|---|---|
| `./config` | `trading_params.yaml`, `models/*.pkl` | il trainer può girare sull'host: un nuovo `champion.pkl` è subito visibile ai container e l'inference lo ricarica a caldo via `model_swap` |
| `./data` | trade, candele live, storico | dashboard e script di analisi host leggono gli stessi file |
| `./logs` | log giornalieri per processo | consultabili dall'host e dalla pagina Logs della dashboard |

Lo stato di Redis (posizioni, capitale, config operativa) e i modelli Ollama vivono invece nei volumi Docker `redis-data` e `ollama-data`: sopravvivono a `docker compose down` e si eliminano solo con `docker compose down -v`. Nota che il Redis containerizzato è **separato** dal Redis di sistema dell'host: il primo avvio Docker parte da stato vuoto (capitale al default, config dal YAML).

## Primo avvio

```sh
# 1. (opzionale) .env con TELEGRAM_TOKEN/TELEGRAM_CHAT_ID per le notifiche

# 2. build + avvio di tutto
docker compose up -d --build

# 3. verifica
docker compose ps            # tutti Up, redis/ollama (healthy)
docker compose logs -f engine
```

Al primo avvio `ollama-init` scarica il modello LLM (~1 GB): finché il pull non è completato il sentiment degrada a neutro (0) — il trading funziona comunque, senza veto di sentiment. La dashboard è su `http://localhost:8501` (o `DASHBOARD_PORT`).

> **Porta occupata**: se sull'host gira già una Streamlit sulla 8501, usa `DASHBOARD_PORT=8502 docker compose up -d dashboard`.

## Operazioni quotidiane

```sh
docker compose ps                        # stato e health dei servizi
docker compose logs -f engine            # log live di un servizio
docker compose restart inference         # riavvio di un singolo servizio
docker compose stop                      # ferma tutto (stato preservato)
docker compose up -d --build             # dopo una modifica al codice
docker compose down                      # rimuove i container (volumi intatti)
```

- **Modifica al codice** → serve il rebuild (`up -d --build`): il codice è *dentro* l'immagine, non montato. Docker ricostruisce solo i layer cambiati (le dipendenze restano in cache finché `requirements.txt` non cambia).
- **Modifica a `config/trading_params.yaml`** → nessun rebuild: la config operativa vive su Redis e si cambia dalla dashboard con hot-reload; lo YAML è il seed del primo avvio.
- **Nuovo champion dal trainer host** → nessun rebuild: bind mount + evento `model_swap`.
- **Crash di un processo** → `restart: unless-stopped` lo rialza; il watchdog notifica su Telegram se un heartbeat resta comunque fermo.

## Differenze rispetto all'avvio manuale

- **Pagina Control ridotta**: stato dei servizi sì (via heartbeat), pulsanti Avvia/Ferma no — si usa `docker compose`. Le azioni che passano da Redis (toggle di configurazione, reset di emergenza) funzionano identiche.
- **Watchdog solo allarme**: niente `--restart`; il riavvio è delegato alle restart policy di compose.
- **Dashboard solo loopback**: la porta è pubblicata su `127.0.0.1` dell'host, coerente con la scelta di sicurezza dell'avvio manuale (la dashboard può chiudere posizioni: non deve essere raggiungibile dalla LAN). Per l'accesso remoto vale il tunnel SSH descritto in [DEPLOYMENT.md](DEPLOYMENT.md#sicurezza).
- **Redis e Ollama containerizzati**: separati dalle istanze di sistema dell'host; lo stato vive nei volumi Docker.
- **Niente `.pyc`**: l'immagine imposta `PYTHONDONTWRITEBYTECODE=1` (il codice appartiene a root, l'utente `hermes` non può scrivere le cache accanto ai sorgenti).

## GPU per Ollama (opzionale)

Su WSL2 o Linux con driver NVIDIA e NVIDIA Container Toolkit, decommenta il blocco `deploy.resources` del servizio `ollama` nel [docker-compose.yml](../docker-compose.yml). Senza GPU il modello gira su CPU: con un modello da 1.5B e un'analisi ogni 5 minuti resta sostenibile.
