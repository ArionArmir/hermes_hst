# Hermes HFT — Analisi dei rischi e piano aggiornato

*2026-07-16. Basata su evidenze verificate: walk-forward 4 fold, ricostruzione bar-per-bar del fold catastrofico, matrice di correlazione a 1 anno, lettura del codice post punti 1-17.*

---

## 1. Il cap direzionale: utile ma non per il rischio che l'ha motivato

**Cosa fa bene.** `max_positions_same_direction=3` protegge dallo scenario "molte posizioni correlate aperte in blocco": se il modello un giorno emettesse 5-7 segnali long simultanei (mai successo nello storico testato, ma possibile con più simboli o soglie più basse), il danno di un ribasso sincrono sarebbe limitato a 3 posizioni invece che a 7. È un limite *strutturale* — non dipende dal capitale, quindi non si allenta col crescere dell'equity come fa il cap di margine.

**Cosa non fa.** Non protegge dal fold catastrofico reale (−33 USDT): lì il picco era di sole 3 posizioni simultanee, ma il modello ha prodotto **6 stop loss consecutivi in ~15 ore** (XRP×3, BNB×2, BTC) — posizioni aperte *in sequenza*, non in blocco. Ogni chiusura in SL liberava lo slot per la successiva. Il cap conta le posizioni *contemporanee*; il danno era *seriale*.

**Limiti strutturali:**
1. **Cieco al tempo**: 3 SL oggi + 3 domani + 3 dopodomani passano tutti.
2. **Cieco all'esito**: non distingue una serie di vincite da una serie di perdite.
3. **Cieco al regime**: quando il modello "non funziona più" nel mercato corrente, il cap non lo sa — continua a permettere 3 posizioni alla volta di un modello che sta sbagliando sistematicamente.
4. **Non selettivo**: stringerlo a 1-2 blocca anche i trade sani (verificato: PnL aggregato peggiora da −20 a −27/−30 sui 4 fold).

**Verdetto**: tenerlo a 3 (assicurazione a costo zero nello storico testato), ma la mitigazione per il rischio *seriale* è un'altra: il circuit breaker (intervento N1 sotto).

---

## 2. Vulnerabilità

Contesto essenziale: il sistema è **paper trading**. I rischi "perdita di capitale" oggi sono virtuali; i rischi *reali* attuali sono (a) trarre conclusioni sbagliate dal paper per la futura decisione di andare live, (b) degradazione silenziosa che invalida i dati raccolti. La priorità riflette questo.

### V1 — Perdite seriali senza freno (il rischio del fold 2) — **Priorità ALTA, impatto 5, sforzo ~0,5 g**
Nessun circuit breaker: dopo N stop loss consecutivi o una perdita giornaliera X, il sistema continua a tradare alla stessa taglia. Il fold 2 dimostra che il modello ha regimi in cui sbaglia ripetutamente; i cooldown esistenti (60 min per simbolo) non fermano la rotazione su simboli diversi (XRP→BNB→BTC). Mitigazione: pausa automatica delle nuove aperture dopo N SL consecutivi in finestra mobile e/o oltre una perdita giornaliera %; ripresa automatica dopo cooldown lungo + notifica Telegram. Va replicato in `backtest_joint` per poterlo tarare.

### V2 — Dati stantii non rilevati — **Priorità ALTA, impatto 4, sforzo ~3 h**
`CandleFeed.get_candles` in caso di errore REST restituisce **la cache senza limite di età**: se Binance REST degrada per ore, l'inference calcola feature su candele vecchie e pubblica segnali "freschi", mentre l'engine li esegue ai prezzi live del WebSocket. Nessun guardrail e nessun alert (il watchdog controlla i tick WS, non l'età delle candele REST). Mitigazione: età massima della cache (es. 2× timeframe) oltre la quale l'inference *tace*, + check nel watchdog.

### V3 — Overfitting della selezione modello/parametri — **Priorità ALTA, impatto 4, sforzo ~1 g**
La promozione champion/challenger decide su **una singola finestra** di validation (~15-20 trade: rumore statistico); soglia e ATR sono tarati sulle stesse famiglie di finestre. Il walk-forward (che già esiste) ha mostrato varianza enorme tra fold (+8 / −33). Mitigazione: promozione gated su **più fold** (es. `walk_forward` interno al trainer: promuovi solo se il challenger vince nella maggioranza dei fold e non peggiora il fold peggiore), non su una finestra sola.

### V4 — Model decay non monitorato — **Priorità ALTA, impatto 4, sforzo ~0,5 g**
Il retraining settimanale esiste, ma tra un retraining e l'altro nessuno confronta il comportamento live con le attese: se il hit rate live crolla al 15% (fold 2 style), lo scopri guardando la dashboard a posteriori. La tabella `signals`+`trades` contiene già tutto il necessario. Mitigazione: check nel watchdog (o job dedicato) su hit rate e PnL rolling degli ultimi N trade vs soglia; alert Telegram. Sinergico con V1 (stesso segnale, risposta diversa: V1 ferma, V4 avvisa).

### V5 — Realismo di esecuzione per il go-live — **Priorità MEDIA, impatto 3 (5 al go-live), sforzo ~0,5 g**
Precisazione doverosa: lo slippage **è già simulato** (2 bps avversi per lato) e le fee pure — la premessa "slippage non simulato" non è corretta. Ciò che manca: (a) **gap attraverso lo stop** — il backtest riempie sempre esattamente al prezzo di SL, e il paper live chiude al primo prezzo osservato oltre lo stop, ma un crash reale con leva 3 può riempire ben peggio; (b) slippage proporzionale alla volatilità invece che fisso; (c) latenza segnale→ordine (nel backtest l'esecuzione all'open della barra successiva è però già *conservativa* rispetto al live). Mitigazione: fill degli SL al peggio tra stop e open/low della barra, slippage scalato sull'ATR. Necessario **prima** di usare i numeri del backtest per decidere il go-live, non prima.

### V6 — Errore umano in configurazione — **Priorità MEDIA, impatto 4, sforzo ~2 h**
`Config` (Pydantic) non ha vincoli di range: `leverage: 100`, `max_exposure: 5.0`, `taker_fee_pct: 0` o una soglia `0.05` verrebbero accettati e applicati a caldo dalla dashboard senza conferme. L'unico check di coerenza esistente è sul max holding. Mitigazione: `Field(ge=…, le=…)` su tutti i parametri di rischio + validazione alla pagina Configurazione con messaggio esplicito.

### V7 — Dipendenze esterne — **Priorità MEDIA (in gran parte già mitigata), impatto 3, sforzo ~2 h residue**
Già coperto: watchdog su heartbeat/tick/Ollama/Redis-down con dedup, riconnessione WS con pubsub fresco, systemd `Restart=always`, degradazione controllata del sentiment, MTU fix in Docker. Residuo: (a) V2 (sopra); (b) singolo endpoint Binance — se `fstream.binance.com` ha un'interruzione lunga con posizioni aperte, il sistema resta cieco sui prezzi (il fallback REST esiste solo per singole richieste); un alert "posizioni aperte + feed muto da X min" già oggi arriverebbe dal watchdog tick, quindi accettabile in paper.

### V8 — Concentrazione per simbolo e correlazione — **Priorità BASSA (già mitigata), impatto 2**
Taglia per posizione uniforme e cappata (`max_position_usdt`), cap di margine, cap direzionale. La correlazione media 0,68 tra i 7 simboli resta un fatto del mercato crypto, non un difetto del sistema: aggiungerne altri non la riduce (verificato coi dati). Nessun intervento consigliato ora.

### V9 — Black swan — **Priorità BASSA in paper (ALTA al go-live), impatto 5, sforzo 0,5 g**
Con leva 3 e SL clampati 1-5%, un flash crash −20% con gap supererebbe qualunque stop; liquidazione non modellata. In paper l'impatto è informativo. Mitigazione al go-live: V5 (gap-fill) + circuit breaker (V1) + eventualmente riduzione leva. Da affrontare nel "pacchetto go-live", non prima.

### V10 — Over-trading — **Priorità BASSA (già mitigata), impatto 2**
Cooldown ingresso 60 min, cooldown+isteresi reverse, fee simulate che puniscono il churn nel backtest, soglia tarata con le fee incluse. Ritmo osservato: ~0,2-0,3 trade/giorno/simbolo. Residuo teorico coperto da V1.

### V11 — Gambler's ruin — **Priorità MEDIA, impatto 5, sforzo ~2 h**
Il sizing si riduce col capitale solo sotto i 300 USDT (finché `capital×0,5 > 150` la taglia resta piena): tra 1000 e 300 il sistema perde a taglia costante, e non esiste un floor di capitale sotto il quale fermarsi. Mitigazione: (a) kill-switch a soglia di equity (es. −30% dal picco → stop aperture + alert), che è di fatto il ramo "drawdown" del circuit breaker V1; (b) opzionale, sizing frazionale (% del capitale invece che nozionale fisso).

---

## 3. Piano di implementazione aggiornato

| # | Intervento | Copre | Priorità | Impatto | Sforzo | Dipendenze |
|---|---|---|---|---|---|---|
| N1 | **Circuit breaker**: pausa aperture dopo N SL consecutivi in finestra mobile, perdita giornaliera max %, drawdown max dal picco; replicato in `backtest_joint` per tararlo; notifiche | V1, V11, parte V9 | **Alta** | 5 | 0,5-1 g | — |
| N2 | **Guardia dati stantii**: età massima cache CandleFeed, inference muta su dati vecchi, check watchdog | V2 | **Alta** | 4 | ~3 h | — |
| N3 | **Promozione multi-fold**: champion/challenger giudicati sul walk-forward (maggioranza dei fold + fold peggiore), non su una finestra | V3 | **Alta** | 4 | ~1 g | riusa walk_forward |
| N4 | **Model-health monitor**: hit rate/PnL rolling live vs soglia, alert Telegram | V4 | **Alta** | 4 | ~0,5 g | tabelle store esistenti |
| N5 | **Vincoli di range su Config** + validazione in dashboard | V6 | Media | 4 | ~2 h | — |
| N6 | **Realismo esecuzione**: gap-fill sugli SL, slippage proporzionale all'ATR | V5, V9 | Media | 3→5 | ~0,5 g | prima del go-live |
| 15 | Processo market_data unico | efficienza | Bassa | 2 | 2-3 g | utile >10 simboli |

**Ordine consigliato**: N1 → N2 → N5 (i tre indipendenti e rapidi, N1 per primo perché indirizza il rischio dimostrato dal fold 2) → N4 → N3 (il più delicato: cambia il criterio di promozione, meglio farlo con N4 già attivo per osservarne gli effetti) → N6 quando si inizia a parlare di go-live → 15 solo se/quando si superano i 10 simboli.

**Dipendenze incrociate**: N1 e N4 leggono le stesse serie (esiti trade recenti) — conviene un modulo condiviso di "salute della strategia". N3 dipende concettualmente da N1: il walk-forward di promozione va eseguito *col circuit breaker attivo*, altrimenti si seleziona il modello su una dinamica di rischio diversa da quella live.

---

## 4. Nota di metodo

Due delle premesse della richiesta si sono rivelate imprecise alla verifica sui fatti, e vale la pena registrarlo: (1) lo slippage **è** simulato (fisso, 2 bps avversi — migliorabile, non assente); (2) l'aggiunta di simboli **non** diversifica questo portafoglio (correlazione media 0,68, misurata su 1 anno di rendimenti orari). La lezione del fold 2 resta la più importante: i meccanismi di rischio *strutturali* (cap di margine, cap direzionale) non sostituiscono un meccanismo *dinamico* che reagisca a ciò che sta accadendo — il circuit breaker è il pezzo mancante.
