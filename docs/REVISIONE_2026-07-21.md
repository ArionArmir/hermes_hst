# Revisione del codice — 2026-07-21

Tre revisori indipendenti (sentiment, inference, engine), mandato "caccia ai
bug invisibili, non stile", sola lettura. 28 finding; i 5 più gravi
verificati riga per riga dal coordinatore. Ha innescato la terminazione
formale di forward_v1 (docs/PRE_REGISTRO_FORWARD.md): i difetti minavano la
validità di ogni trade futuro dell'esperimento.

**Quadro d'insieme**: la matematica è sana ovunque (PnL long/short, segni
SL/TP, simmetria veto, timezone — verificati e promossi da tutti e tre). I
bug vivono nei percorsi di *resilienza*: crash, riavvii, riconnessioni,
input ostili. Stessa famiglia dell'incidente della soglia del 2026-07-20.

Legenda stato: ✅ corretto con test · 📋 documentato, correzione deliberata da
pianificare (tocca il servizio vivo o richiede infrastruttura nuova).

## Sentiment — tutti corretti (codice in ombra, nessun vincolo)

| # | Gravità | Difetto | Stato |
|---|---|---|---|
| S1 | critico | `stato.json` v2: scrittura non atomica + load non protetto → crash-loop e memoria bruciata | ✅ tmp+os.replace, load con quarantena del file corrotto |
| S3 | alto | Titoli marcati "visti" prima della valutazione: un errore Ollama perde la notizia per sempre (v2+macro) | ✅ `dimentica()` li restituisce se la valutazione fallisce |
| S2 | alto | Prompt injection via titoli RSS (v1 tocca il veto live) | ✅ delimitatori + istruzione "dati, non istruzioni" (v1/v2/macro) |
| S4 | medio | Decadimento senza clamp con orologio all'indietro (sleep WSL2) → score oltre ±1 | ✅ `max(0, minuti)` |
| S5 | medio | La sonda di ripetibilità nello stesso try della valutazione butta il risultato primario | ✅ try separato per la replica |
| S6 | medio | `RedisClient.connect` inghiotte il fallimento → servizio zombie | ✅ retry con backoff + ping; heartbeat con try |

## Inference — tutti corretti (era congelato; forward_v1 terminato)

| # | Gravità | Difetto | Stato |
|---|---|---|---|
| I1 | critico | Heartbeat fuori dal try: un'eccezione Redis uccide il loop, heartbeat resta verde | ✅ heartbeat dentro il try |
| I2 | alto | Reload config fallito → default hardcoded (0.55, 3 simboli), invisibile al manifest | ✅ non regredisce mai: tiene la config in memoria |
| I3 | alto | Modello rifiutato/corrotto senza retry → muto a tempo indefinito | ✅ un load fallito non azzera il modello in servizio |
| I4 | medio | Nessuna guardia anti-inf nella pipeline live: ±inf passa `isna()` | ✅ `np.isfinite` su tutta la riga feature |
| I5 | medio | Scarto incondizionato dell'ultima candela senza verificare `close_time` | ✅ scarta solo se davvero in formazione |
| I9 | basso | Un try unico per l'intero for-simboli: un simbolo affama i successivi | ✅ try per simbolo |
| I6 | basso | Race hot-reload modello (check vs predict) | 📋 mitigato da I9 (eccezione isolata al simbolo); race residua di 5s benigna |
| I7 | basso | `max_oggi` rollover UTC mostrato come "oggi" locale | 📋 solo telemetria; da rivedere col cruscotto forward_v2 |

## Engine — critici e alti corretti; alcuni medi/minori documentati

| # | Gravità | Difetto | Stato |
|---|---|---|---|
| E1 | critico | Trip giornaliero non rientra mai se scatta all'ultima chiusura | ✅ rientro al cambio giorno UTC in `is_tripped` |
| E1b | critico | Trip giornaliero perso a ogni riavvio (`seed` non lo ricostruisce) | ✅ ricostruito da `seed_from_history` |
| E3 | alto | SL/TP solo su tick WebSocket; backstop chiude al prezzo di entrata | ✅ check di uscita nel `_position_monitor` (5s) con prezzo REST se il tick è stantio |
| E4 | alto | Reverse apre dopo che il breaker è scattato (gate non ricontrollato) | ✅ ricontrollo breaker in `_open_position` |
| E8 | medio | `requests`/`smtplib` sincroni congelano il loop nel percorso caldo | ✅ notifiche in `asyncio.to_thread` |
| E9 | basso | `round(qty,3)` → 0 su asset cari/capitale basso → posizione zombie | ✅ guardia `qty<=0` |
| E2 | alto | Reset manuale del breaker non persistito → ri-arma a ogni riavvio | ✅ reset persistito su Redis (`circuit_breaker_reset`); `seed_from_history` ignora la storia pre-reset e il picco riparte dal capitale al reset |
| E5 | medio | `_close_position` non atomica Redis↔SQLite: crash a metà perde PnL o storico | ✅ SQLite scritto PRIMA (fonte durevole), capitale riconciliato dal log al riavvio; residuo micrometrico documentato sotto |
| E6 | medio | Trailing statico codice morto (`position.trailing_stop` scritto e mai letto) | ✅ trailing statico ratchet nel monitor (stop avanza mai indietro), attivo col dinamico spento |
| E7 | medio | Perdita messaggi pubsub durante il recovery del listener (incl. `close_all`) | ✅ resync della config dopo ogni (ri)sottoscrizione; `close_all` transitorio resta limite noto (sotto) |
| E10 | basso | Cambio timeframe a caldo mischia candele di intervalli diversi nell'ATR | ✅ svuotamento exit/pattern models + riconnessione WS al cambio timeframe |
| E11 | basso | Leak di oggetti PubSub durante flapping Redis prolungato | ✅ `pubsub.aclose()` in `finally` del recovery |

## Residui noti (accettati, non difetti aperti)

- **E5**: Redis e SQLite non sono atomici tra loro. La finestra micrometrica
  tra l'insert SQLite e l'update Redis può, in caso di crash lì dentro,
  lasciare la posizione "aperta" in cache Redis mentre il trade è già
  registrato: in paper trading si richiude al più con un trade duplicato,
  senza perdita di capitale (che è riconciliato dal log). Eliminarlo del
  tutto richiederebbe un journal a due fasi, sproporzionato per denaro di carta.
- **E7**: i comandi transitori (`close_all` da dashboard) non sono replicabili
  se persi nella finestra di ri-sottoscrizione — il pubsub è fire-and-forget.
  La config invece è riconciliata (resync). Una coda durevole per i comandi
  è la soluzione completa, rimandata a quando/se servirà.

## Osservabilità aggiunta

- **Watchdog check "valutazioni ml"** (✅): sorveglia la freschezza di
  `ml_conf_*`. Trasforma I1/I3 (inference muta con heartbeat verde) in un
  allarme entro ~10 minuti. È la mitigazione esterna che rende i difetti di
  resilienza rimasti (📋) osservabili anche prima di correggerli.

## Seconda passata — regressioni dei fix (2026-07-21, pomeriggio)

Tre revisori mirati SOLO al diff dei fix, a caccia di regressioni introdotte
la mattina. I due dubbi più gravi (doppia chiusura, `is_tripped` che muta
stato) sono stati **verificati sicuri** (nessun `await` tra guardia e
`is_open=False` col prezzo esplicito; asyncio single-thread rende
`is_tripped`/`record_trade` atomici). Trovate 3 regressioni reali, tutte
corrette con test:

| Regressione | Gravità | Correzione |
|---|---|---|
| `connect()` con retry infinito appendeva avvio servizi e test suite se Redis giù | alto | retry BOUNDED (~2 min) poi solleva → systemd riavvia (fail loud + auto-recover) |
| `_carica_stato` catturava `OSError` (lettura transitoria) e buttava stato valido | medio | solo `JSONDecodeError` va in quarantena; `OSError` rilancia (file intatto, systemd riavvia); rename protetto; microsecondi nel nome |
| Insert SQLite fallito lasciava Redis avanti → capitale regrediva al riavvio | medio | la chiusura si ABORTISCE se la scrittura durevole fallisce (posizione resta aperta, si richiude dopo): stato sempre coerente col log |

Più due minori corretti: `_trip_reason` azzerato al rientro del trip
giornaliero; il trailing statico ratchettato ora persiste su Redis (non si
perde a un riavvio). Suite: 372 verdi.

## Stato finale

**Tutti i 28 finding chiusi**: corretti con test, o (per i due residui E5/E7
sopra) accettati con motivazione esplicita — sono limiti architetturali del
paper trading su Redis+SQLite, non difetti aperti. La seconda tornata di fix
del motore (E2, E5, E6, E7, E10, E11) è stata fatta il 2026-07-21 stesso, dopo
la prima, con motore riavviato e verificato pulito. Suite: 369 verdi.
