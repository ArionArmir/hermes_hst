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
| E2 | alto | Reset manuale del breaker non persistito → ri-arma a ogni riavvio | 📋 richiede persistenza del reset (Redis/file); il re-trip è un fallimento SICURO (blocca, non perde) — da fare deliberatamente |
| E5 | medio | `_close_position` non atomica Redis↔SQLite: crash a metà perde PnL o storico | 📋 riordino verso "DB prima, poi Redis" sul servizio vivo — cambio delicato, da pianificare |
| E6 | medio | Trailing statico codice morto (`position.trailing_stop` scritto e mai letto) | 📋 decidere: implementarlo o rimuovere il campo (oggi il trailing vive solo in `dynamic_exit`) |
| E7 | medio | Perdita messaggi pubsub durante il recovery del listener (incl. `close_all`) | 📋 pubsub è fire-and-forget; un resync dello stato dopo la ri-sottoscrizione è la cura, da progettare |
| E10 | basso | Cambio timeframe a caldo mischia candele di intervalli diversi nell'ATR | 📋 svuotare exit/pattern models + riconnettere WS al `config_updated` |
| E11 | basso | Leak di oggetti PubSub durante flapping Redis prolungato | 📋 `pubsub.close()` nel recovery |

## Osservabilità aggiunta

- **Watchdog check "valutazioni ml"** (✅): sorveglia la freschezza di
  `ml_conf_*`. Trasforma I1/I3 (inference muta con heartbeat verde) in un
  allarme entro ~10 minuti. È la mitigazione esterna che rende i difetti di
  resilienza rimasti (📋) osservabili anche prima di correggerli.

## I 📋 rimasti: perché non oggi

Sono tutti sul motore, che ora gira come **servizio di collaudo** (non più
esperimento). Toccano atomicità, pubsub e lo stato del breaker persistito:
cambi che vanno fatti con i loro test e senza fretta, non ammucchiati in una
passata. Il check watchdog li rende visibili nel frattempo. Ordine
consigliato quando si riprende: E5 (atomicità chiusura) → E7 (resync pubsub)
→ E2 (persistenza reset) → E6/E10/E11 (minori).
