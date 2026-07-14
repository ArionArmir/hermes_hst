# Hermes HFT — Piano di miglioramento strategico

*Analisi completa del 2026-07-14, basata su: tutto il codice in `src/`, `dashboard/`, `config/`, il modello `champion.pkl`, i log del test overnight in corso e `data/trades_history.csv`.*

---

## 1. Riassunto esecutivo

Il sistema è **architettonicamente sano** per essere un progetto in evoluzione: tre processi disaccoppiati via Redis, config centralizzata con hot-reload, heartbeat, dashboard di controllo, champion/challenger. Il refactoring recente (simboli dinamici, riconnessione WS, split temporale per simbolo nel training) è di buona qualità.

Però l'analisi ha rivelato **tre difetti fondamentali che invalidano la pipeline ML in produzione**, e i dati del test in corso lo confermano empiricamente:

1. **Train/serve skew grave**: il modello è addestrato su candele 1h ma in inference riceve feature calcolate sugli ultimi ~100 tick (secondi di dati). Peggio: due feature (`sma_20`, `sma_50`) sono passate al modello in **scala completamente diversa** (valori grezzi in training ~63.000 per BTC, ratio ~0 in inference). XGBoost non se ne accorge perché l'inference passa un ndarray senza nomi di colonna.
2. **Mismatch di orizzonte**: il target del training è "+0,5% entro 5 candele da 1h" (5 ore), ma l'engine chiude tutto a 60 minuti (`max_holding_minutes`). Il modello predice qualcosa che la strategia non incassa mai.
3. **ATR su dati sintetici**: l'engine inventa high/low come `price ± 0,2%`, quindi l'ATR è circa costante e i moltiplicatori per-simbolo vengono calibrati su dati fabbricati.

**Conferma empirica dal test in corso**: tutti i 14 trade di oggi sono long, aperti in blocco, chiusi tutti a esattamente 60 minuti con motivo `MAX_HOLDING`, e riaperti subito dopo. La strategia effettiva oggi è "compra tutto, ruota ogni ora": il PnL misura il drift del mercato, non il modello.

**Raccomandazione**: prima di ottimizzare qualunque altra cosa (scalabilità, deployment, dashboard), sistemare la coerenza train/serve e l'orizzonte del target, e costruire un backtester con commissioni. Senza questo, ogni test notturno misura rumore.

---

## 2. Architettura

### Punti di forza
- Separazione netta Engine / Inference / Sentiment con Redis come bus: giusto per questo scopo.
- `Config` Pydantic unica, hot-reload via `config_updated`, versionamento config.
- Heartbeat per processo (`heartbeat_*`, `last_tick_*`) già in place.
- Dashboard come "guscio sottile" con pagine separate, `process_manager` che riusa `start.sh`.
- Commenti che documentano *perché* (es. riconnessione WS, split per simbolo): ottima pratica.

### Problemi e proposte

**A1 — Feature engine duplicato (causa radice dello skew).** Esistono due implementazioni indipendenti: `src/training/feature_engine.py` (pandas, su candele) e `src/inference/feature_engine.py` (numpy, su tick). Divergeranno sempre. Proposta: **un solo modulo** `src/shared/features.py` usato da entrambi, che prende in input un DataFrame OHLCV e restituisce un DataFrame di feature **con nomi di colonna**. L'inference costruisce le candele (l'`OHLCAggregator` esiste già!) e chiama la stessa funzione del training.

```python
# src/shared/features.py — unica fonte di verità
FEATURE_COLS = ['rsi', 'sma20_ratio', 'sma50_ratio', 'volatility', ...]

def compute_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Identico per training e inference. Input: candele OHLCV."""
    ...

# inference/main.py
candles = self.candle_store[symbol].to_dataframe()   # ultime N candele
feats = compute_features(candles).iloc[[-1]][FEATURE_COLS]  # DataFrame con nomi!
prob = self.model.predict_proba(feats)[0][1]
```

Passare un DataFrame (non ndarray) fa sì che XGBoost **validi i nomi delle feature** e sollevi un errore in caso di mismatch, invece di predire silenziosamente su spazzatura.

**A2 — Doppia connessione WebSocket sugli stessi stream.** Engine e Inference aprono ciascuno una connessione `@trade` sugli stessi 7 simboli: doppio traffico, doppio parsing, due punti di rottura. Evolutivo: per ora va bene, ma a 20-30 simboli conviene un processo `market_data` unico che consuma i WS e pubblica su Redis (`tick:{symbol}` o candele 1m). Engine e Inference diventano consumatori puri. (Vedi §4.)

**A3 — Persistenza CSV con riscrittura completa.** `_save_trade_to_file` e `_save_sentiment` leggono l'intero CSV, concatenano e riscrivono: O(n²) nel tempo e corruttibile se il processo muore a metà scrittura. Proposta: append puro (come già fa `OHLCAggregator._flush`) o meglio **SQLite** (`data/hermes.db`, tabelle `trades`, `sentiment`, `signals`) — zero dipendenze, transazionale, interrogabile dalla dashboard.

**A4 — Recovery del listener Redis fragile.** In caso di eccezione, `_redis_listener` ricrea il task ma riusa lo stesso oggetto pubsub (potenzialmente rotto) e `RedisClient` condivide un singolo `_pubsub` per tutte le subscribe. Proposta: ricreare pubsub e re-subscribe dentro un loop `while self.running` con backoff, come per il WS.

**A5 — `capital` hardcoded a 1000 e mai aggiornato.** Il PnL dei trade non modifica `self.capital`, quindi sizing ed esposizione sono calcolati su un capitale fittizio costante. In paper trading va aggiornato a ogni chiusura; in live va letto dall'exchange.

Effort: A1 ~1 giorno, A3 ~mezza giornata, A4 ~2 ore, A5 ~1 ora. A2 ~2-3 giorni (rimandabile).

---

## 3. Strategia di trading

### Problemi (in ordine di gravità)

**S1 — Il target non corrisponde alla strategia.** `target = (close.shift(-5)/close - 1 > 0.005)` su candele 1h = "sale dello 0,5% in 5 ore". Ma `max_holding_minutes: 60`. Il modello viene premiato per pattern che si realizzano in 5 ore e la posizione viene chiusa dopo 1. Proposta: allineare — o target a 1 barra 1h (o passare a candele 15m con target a 4 barre), o alzare il max holding all'orizzonte del target. Decidere prima **qual è l'orizzonte della strategia** e derivare tutto il resto da lì.

**S2 — Gli short non sono giustificati dal modello.** Il modello è binario "sale > 0,5% / non sale". `prob < 0.4` significa "improbabile che salga dello 0,5%", **non** "probabile che scenda": il mercato laterale soddisfa entrambe. Aprire short su questo segnale è scommettere su informazione che il modello non ha. Proposte (in alternativa):
- Target a 3 classi: `up` (> +0,5%), `down` (< −0,5%), `flat` — short solo su `P(down)` alta;
- oppure due modelli binari simmetrici (uno per lato);
- nel frattempo, **disabilitare i sell** o alzare la soglia short molto oltre 0,4.

**S3 — Nessuna protezione flip-flop / cooldown.** L'inference rivaluta ogni 5 s; il reverse chiude e riapre in 0,5 s. Con prob che oscilla intorno a 0,6 si può invertire posizione ogni pochi secondi pagando (in live) commissioni + spread a ogni giro. Proposta engine-side:

```python
# engine: stato per simbolo
self.last_entry_time: Dict[str, datetime] = {}
self.reverse_cooldown_minutes = 15

# in _on_signal, prima del reverse:
last = self.last_entry_time.get(signal.symbol)
if last and (now - last).total_seconds() < self.reverse_cooldown_minutes * 60:
    logger.info(f"⏳ Cooldown attivo per {signal.symbol}, reverse ignorato")
    return
```
Più isteresi sulle soglie: entrare a prob > 0,60 ma invertire solo a prob opposta > 0,65.

**S4 — Sentiment usato in modo incoerente.**
- `weighted_confidence = (1-w)·conf + w·|sentiment|`: il **valore assoluto** aumenta la confidenza anche quando il sentiment è *contrario* al trade (sentiment −0,8 su un BUY alza la confidenza!).
- Il filtro è asimmetrico: blocca BUY con sentiment < −0,5 ma non blocca SELL con sentiment > +0,5.
- Copre solo BTC/ETH/SOL: prompt Ollama e mappa `sentiment_by_asset` sono hardcoded; DOGE/XRP/BNB/TRX usano silenziosamente l'aggregato.

Proposta: sentiment **direzionale** e simboli dinamici da config:

```python
directional = asset_sentiment if signal.action == 'buy' else -asset_sentiment
weighted_confidence = (1 - w) * signal.confidence + w * max(0.0, directional)
if directional < -0.5:   # simmetrico per entrambi i lati
    return  # segnale contro sentiment forte
```
E nel sentiment: costruire il prompt iterando sui simboli della config (base asset estratto da `symbol[:-4]`).

**S5 — Nessun controllo di esposizione a livello portafoglio.** Sizing per simbolo: `min(50·3, 1000·0.5) = 150 USDT`; con 7 simboli → 1.050 USDT potenziali su 1.000 di capitale, e le 7 crypto sono altamente correlate: 7 long simultanei = un'unica grande scommessa sul beta di BTC (i log di oggi lo mostrano: aperture in blocco nello stesso minuto). Proposta: cap aggregato in `_open_position`:

```python
open_notional = sum(p.quantity * self.latest_prices.get(s, p.entry_price)
                    for s, p in self.positions.items() if p.is_open)
if open_notional + position_size > self.capital * self.max_exposure * self.leverage:
    logger.warning("⚠️ Cap esposizione portafoglio raggiunto")
    return
```
In seguito: limite sul numero di posizioni concorrenti nella stessa direzione.

**S6 — ATR calcolato su high/low inventati.** `engine/main.py:220-221` sintetizza `high = price·1.002, low = price·0.998` per ogni tick → il true range è ~costante allo 0,4% del prezzo, l'ATR non misura la volatilità reale e i moltiplicatori per-simbolo (5.0/5.5 ecc.) compensano un artefatto. Proposta: alimentare `ATRExitModel` con **candele 1m reali** (lo stream `@kline_1m` di Binance dà high/low veri, oppure riusare `OHLCAggregator` che già costruisce candele corrette dai tick). I moltiplicatori andranno ricalibrati dopo il fix — i valori attuali non sono trasferibili.

**S7 — Manca un backtester.** Oggi l'unica validazione è l'accuratezza di classificazione + test notturni live (lenti, non ripetibili, un solo regime di mercato). Serve un backtest event-driven minimale che simuli la logica dell'engine (soglie, reverse, cooldown, ATR exit, max holding) su candele storiche **con commissioni e slippage** (Binance Futures taker ~0,05%; il round-trip su reverse costa ~0,2% con leva 3). Metriche: PnL netto, Sharpe, max drawdown, hit rate, turnover. È il prerequisito per decidere qualunque soglia. Effort: ~2-3 giorni, il ritorno è enorme.

---

## 4. Performance e scalabilità

Stato attuale: a 7 simboli il sistema regge (i processi girano, WSL2). Colli di bottiglia in vista di 20-30 simboli:

| Collo di bottiglia | Oggi | A 30 simboli | Fix |
|---|---|---|---|
| Stream `@trade` (ogni singolo trade) | ok | BTC+ETH da soli fanno migliaia di msg/s nei picchi | passare a `@aggTrade` o meglio `@kline_1m` (1 msg/s/simbolo, con high/low veri → risolve anche S6) |
| 2 `redis.set` per tick (`latest_price_*`, `last_tick_*`) | decine/s | migliaia/s | throttle: scrivere al massimo ogni 250 ms per simbolo |
| Doppio WS engine+inference | 2 conn | 2 conn ma doppio carico CPU parsing | processo `market_data` unico (A2) |
| Inference sequenziale ogni 5 s | ~ms | ok comunque (XGBoost su 30 righe è nulla) | nessuno |
| Log inference a DEBUG | ~1 MB/2h | ~10-15 MB/giorno×n | portare il file a INFO |
| CSV riscritti interi (A3) | ok | degrada | SQLite/append |

Nota: Binance limita a 200 stream per connessione e 1024 connessioni: nessun problema hard fino a ~100 simboli. Il vero limite di scala non è tecnico ma **statistico**: un modello pooled su 30 simboli eterogenei richiede feature rigorosamente scale-invariant (vedi M2).

---

## 5. ML e feature engineering

**M1 — Skew train/serve (il problema n.1 del progetto).** Quattro mismatch verificati confrontando i due feature engine e il champion:

| Feature | Training (candele 1h) | Inference (ultimi ~100 tick) |
|---|---|---|
| `sma_20`, `sma_50` | **valore grezzo in scala prezzo** (~63.000 BTC, ~0,32 TRX) | `sma/price − 1` (~0) |
| `returns` | rendimento della candela 1h | rendimento tick-su-tick (~10⁻⁵) |
| `obv_norm` | `obv / rolling(50).mean(obv) − 1` | `obv / mean(volume[-20:]) − 1` (formula diversa) |
| tutte le finestre | RSI(14) = 14 **ore** | RSI(14) = 14 **tick** (secondi) |

Ho verificato il champion: `get_booster().feature_names = ['rsi', 'sma_20', 'sma_50', ...]` con `sma_20` al 3° posto per importanza (0,072) — il modello *usa* quella feature grezza, e in produzione riceve ~0 al suo posto. Le predizioni live sono di fatto scorrelate da ciò che il modello ha imparato.

Fix (insieme ad A1): feature uniche, tutte scale-invariant (`sma20_ratio = close/sma20 − 1` in *entrambi* i posti), inference su candele dallo stesso timeframe del training, DataFrame con nomi. **Questo invalida anche il confronto champion/challenger fatto finora in live**: andrà rifatto dopo il fix.

**M2 — Scale-invariance per il pooled model.** L'Approccio A (modello unico multi-simbolo) è la scelta giusta a questa scala, ma regge solo se *nessuna* feature porta la scala del simbolo. Oggi `sma_20`/`sma_50` grezze la portano (il modello può "riconoscere" il simbolo dal prezzo). Dopo il fix M1, aggiungere un test automatico: le distribuzioni di ogni feature per simbolo devono sovrapporsi (es. controllo su mediana/IQR per simbolo in `train_all_models.py`, con warning se divergono di ordini di grandezza).

**M3 — Metrica di selezione champion.** L'accuratezza su un target sbilanciato (quante candele 1h fanno +0,5% in 5 barre? probabilmente ~30-40%) premia il modello che dice sempre "no". Sostituire con: AUC + **PnL simulato dal backtester (S7) al netto delle fee** come criterio di promozione. Il challenger dovrebbe anche girare in **shadow mode** (predizioni loggate, non tradate) per qualche giorno prima dello swap.

**M4 — Hot-reload del modello mancante.** `trainer._swap_model()` pubblica su `model_swap`, ma **nessuno è iscritto a quel canale**: l'inference carica il modello solo all'avvio. Fix da 10 righe: aggiungere `model_swap` alle subscribe dell'inference e chiamare `_load_model()`.

**M5 — Miglioramenti successivi (dopo M1-M4, non prima):** early stopping + `eval_set` nel fit; walk-forward validation invece di un singolo split 80/20; feature di regime (volatilità realizzata su finestra lunga, distanza dal massimo/minimo di periodo, ora del giorno); calibrazione delle probabilità (Platt/isotonic) visto che le soglie 0,6/0,4 assumono probabilità calibrate — XGBoost out-of-the-box non lo è.

---

## 6. Dashboard e UX

La struttura (4 pagine, guscio sottile) è buona. Mancano, in ordine di utilità:

1. **Equity curve e PnL cumulativo** per simbolo e totale (da `trades_history` — o dalla futura tabella SQLite). È il numero che conta; oggi bisogna aprire il CSV.
2. **Pannello "salute"**: heartbeat età (engine/inference/sentiment), età ultimo tick, stato WS — con soglie colorate (verde < 30 s, rosso > 2 min). I dati su Redis ci sono già tutti.
3. **Storia dei segnali** (anche quelli filtrati e il motivo: confidenza bassa, pattern reject, cooldown): fondamentale per capire *perché* il bot non sta tradando. Oggi serve grep nei log.
4. **Info modello**: data training, accuratezza/AUC, feature importance, esito ultimo confronto champion/challenger.
5. Distinzione visiva **paper/live** ben evidente (banner) quando arriverà il trading reale.

Effort: 1-2 giorni complessivi, nessuna dipendenza.

---

## 7. Sicurezza e affidabilità

- **Oggi il rischio è basso** perché il sistema è paper-only: `_place_limit_order`/`_place_close_order` sono stub che loggano e basta, nessuna API key Binance è in uso. Va detto chiaramente: *il test notturno non tocca denaro*.
- **Prima del live**: API key in `.env` con permessi solo-futures e senza withdrawal, IP whitelist Binance; mai loggare la chiave; Redis con `requirepass` anche su localhost (la dashboard può chiudere posizioni: oggi chiunque acceda alla macchina può farlo via redis-cli).
- **Recovery**: le posizioni sono già persistite su Redis e ricaricate all'avvio (bene). Manca: al riavvio in live, **riconciliazione** con le posizioni reali sull'exchange (fonte di verità = exchange, non Redis).
- **Watchdog assente**: gli heartbeat esistono ma nessuno li guarda. Proposta minima (~2 ore): script `watchdog.py` in cron ogni minuto che legge `heartbeat_*` da Redis e manda Telegram (il `Notifier` c'è già) se un processo è stale > 2 min; opzionalmente riavvia via `process_manager`.
- `dump.rdb` nella root del repo: aggiungere a `.gitignore` e configurare `dir` di Redis fuori dal progetto.
- La nota "logs solo via mv, mai troncamento" in `prepare_overnight_test.sh` è preziosa: spostarla anche in `docs/DEVELOPMENT.md`.

---

## 8. Deployment e operazioni

Percorso consigliato per WSL2/server Linux singolo (evolutivo, niente Kubernetes):

**Fase 1 — systemd (subito, ~mezza giornata).** Un'unit per servizio con `Restart=always`; risolve auto-riavvio, avvio al boot, log via journald:

```ini
# /etc/systemd/system/hermes-engine.service
[Unit]
Description=Hermes HFT Engine
After=redis-server.service network-online.target
Requires=redis-server.service

[Service]
User=alexbi
WorkingDirectory=/home/alexbi/hermes_hft
EnvironmentFile=/home/alexbi/hermes_hft/.env
ExecStart=/home/alexbi/hermes_hft/venv/bin/python -m src.engine.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
(idem per inference/sentiment/dashboard; `process_manager.py` della dashboard passa a `systemctl --user` o sudo mirato).

**Fase 2 — Docker Compose (quando si va su un VPS).** Servizi: redis, engine, inference, sentiment, dashboard, ollama; volume per `data/` e `config/`; healthcheck che legge gli heartbeat. Effort ~1 giorno.

**Fase 3 — retraining schedulato.** Cron settimanale: scarica dati aggiornati → `train_all_models.py` → il challenger va in shadow mode → promozione solo se batte il champion sul backtest con fee (M3).

---

## 9. Piano di implementazione

| # | Intervento | Priorità | Effort | Dipendenze |
|---|---|---|---|---|
| 1 | Feature engine unificato su candele + DataFrame con nomi (A1+M1+M2) | **Alta** | 1-1,5 g | — |
| 2 | Allineare target/orizzonte strategia (S1) + retrain | **Alta** | 0,5 g | 1 |
| 3 | ATR su candele 1m reali (S6) + ricalibrare moltiplicatori | **Alta** | 0,5 g | — |
| 4 | Disabilitare short o target 3-classi (S2) | **Alta** | 0,5-1 g | 1,2 |
| 5 | Cap esposizione portafoglio + capital aggiornato (S5, A5) | **Alta** | 0,5 g | — |
| 6 | Backtester con fee/slippage (S7) → nuova metrica champion (M3) | **Alta** | 2-3 g | 1,2 |
| 7 | Cooldown/isteresi reverse (S3) | Media | 2 h | — |
| 8 | Sentiment direzionale + simboli dinamici (S4) | Media | 0,5 g | — |
| 9 | Hot-reload modello via `model_swap` (M4) | Media | 1 h | — |
| 10 | Watchdog heartbeat → Telegram (§7) | Media | 2 h | — |
| 11 | systemd units (§8 fase 1) | Media | 0,5 g | — |
| 12 | SQLite per trades/segnali/sentiment (A3) | Media | 0,5 g | — |
| 13 | Dashboard: equity curve, salute, storia segnali (§6) | Media | 1-2 g | 12 (meglio) |
| 14 | Redis listener robusto (A4), throttle scritture tick | Bassa | 0,5 g | — |
| 15 | Processo market_data unico + kline stream (A2, §4) | Bassa | 2-3 g | utile solo >10 simboli |
| 16 | Docker Compose (§8 fase 2) | Bassa | 1 g | 11 |
| 17 | Calibrazione probabilità, walk-forward, feature di regime (M5) | Bassa | 2-3 g | 6 |

**Sequenza consigliata**: 1→2→3→4→5 (una settimana: rendono il sistema *misurabile correttamente*) → 6 (il backtester diventa il banco di prova di tutto il resto) → 7-13 in parallelo secondo convenienza.

---

## 10. Conclusioni e prossimi passi

Il lavoro di refactoring recente ha prodotto una base operativa solida: processi disaccoppiati, config dinamica, riconnessioni robuste, controllo da dashboard. Il problema non è l'impalcatura ma **il contenuto predittivo**: per lo skew train/serve (M1) il modello in produzione non sta ricevendo le feature su cui è stato addestrato, e per il mismatch di orizzonte (S1) anche un modello perfetto non verrebbe incassato dalla strategia. I dati del test odierno (tutti long, tutti chiusi a 60' esatti per MAX_HOLDING, riaperti subito) sono coerenti con questa diagnosi.

Prossimi passi concreti:
1. Lasciar finire il test notturno *sapendo che misura il drift di mercato, non il modello* — utile comunque come test di stabilità operativa (riconnessioni, heartbeat, memoria).
2. Implementare gli interventi 1-5 della tabella.
3. Costruire il backtester (6) e ricalibrare soglie/moltiplicatori su di esso.
4. Solo dopo: nuovo test notturno "vero", poi le voci a priorità media.
