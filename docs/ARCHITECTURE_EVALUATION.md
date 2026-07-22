# Hermes HFT — Valutazione del cambio di architettura

*2026-07-16. Redatta dopo il fallimento misurato di tre interventi incrementali sul modello 1h (#4 soglie, #1 feature, #2 simboli). Tutte le cifre provengono da backtest_joint e walk-forward sui dati reali della sessione; nessuna è stimata a mano se non dove esplicitamente indicato.*

---

## 1. Sintesi esecutiva

Il modello direzionale 1h ha raggiunto un **soffitto misurato**: tre vie di miglioramento incrementale sono state tentate con rigore e tutte respinte dal walk-forward. Il limite non è di taratura ma **strutturale** — scarsità di segnale, universo mono-correlato, modello unico che non si adatta al regime.

La scoperta costruttiva: la **correlazione 0,68-0,89 che rende impossibile la diversificazione è esattamente ciò che una strategia market-neutral di mean-reversion su spread (stat-arb / pairs) sfrutterebbe**. Una misura preliminare mostra Sharpe 1,2-1,7 su 3 coppie su 5, ed è per costruzione immune al crollo correlato che affossa la strategia direzionale nel fold peggiore. È l'unico candidato architetturale con evidenza empirica positiva, e trasforma il principale punto debole del sistema nell'edge.

**Raccomandazione**: valutare a fondo la pista market-neutral come nuovo *layer di segnale*, preservando integralmente l'infrastruttura di rischio/operatività già costruita (che è agnostica alla strategia). Non è "buttare via tutto": il pivot riguarda il generatore di segnali, non il sistema.

---

## 2. Perché l'architettura attuale è al soffitto (evidenza)

### 2.1 I tre fallimenti, con i numeri

| Intervento | Ipotesi | Esito walk-forward (totale / fold peggiore) | Verdetto |
|---|---|---|---|
| Baseline (14 feature, 7 simboli) | — | −0,68 / −8,78 | riferimento |
| **#4 Soglie separate long/short** | short a soglia più bassa aggiungono edge | il "+3" di singola finestra sparisce: guadagno concentrato in 1 fold, 3/4 peggiorano | respinto |
| **#1 Feature di regime + calendario** | dare contesto → modello selettivo nei regimi tossici | 22 feat: −2,03 / −7,85; solo regime (18): **−51,45 / −36,97** | respinto |
| **#2 Espansione simboli** | più simboli = più opportunità | +FIL (8 simboli): **−58,94 / −42,91**; +4 (11): −7,29 / −18,37 | respinto |

Ogni tentativo peggiora la metrica di sicurezza (fold peggiore). Il pattern è coerente e non casuale.

### 2.2 I limiti strutturali diagnosticati

1. **Scarsità di segnale.** Solo lo **0,22% delle barre** supera la soglia 0,55; ~15 trade per finestra di validation. Il target "±0,5% in 5 barre" produce una classe "flat" dominante (~46% a 1h, 88% a 5m). Il modello è un rilevatore di eventi rari su un bersaglio quasi sempre piatto. Su una base così rada, aggiungere feature offre soprattutto modi di overfittare, non di trovare più segnali (confermato da #1).

2. **Soffitto di correlazione.** L'intero universo liquido è un unico paniere correlato (screening: candidati 0,69-0,89; media interna 0,68; TRX a 0,4 è un outlier irripetibile). La diversificazione è impossibile e l'esposizione di portafoglio è intrinsecamente concentrata (confermato da #2).

3. **Modello unico cieco al regime.** Un solo XGBoost pooled per tutti i simboli e regimi. Nel fold 2 (mercato in discesa correlato) **fallisce su entrambi i lati** (long 0/8). Le feature di regime non risolvono — lo peggiorano (#1). Aggiungere simboli al pool degrada la calibrazione per l'intero portafoglio (#2).

4. **Classe di feature esaurita.** Le 14 feature tecniche catturano pattern di swing orari; la loro alpha è quella che è. Regime/calendario overfittano.

5. **Esposizione direzionale = beta.** In validation tutti i 15 trade sono long: la strategia cattura il beta del mercato, e nel crollo correlato (fold 2) non ha copertura. È la radice del rischio di coda che i cap e il circuit breaker possono contenere ma non eliminare.

**Conclusione**: i punti 1-5 non sono difetti correggibili con parametri o feature — sono proprietà della coppia (target direzionale a 5 barre) × (universo mono-correlato). Cambiarle richiede un cambio di *paradigma di segnale*, non di taratura.

---

## 3. Alternative architetturali valutate

Per ciascuna: descrizione, evidenza/prior, sforzo, rischio, compatibilità col vincolo "riusare l'infrastruttura esistente".

### A. Market-neutral / stat-arb su spread (pairs trading) — **candidato principale**
- **Idea**: invece di prevedere la direzione di un simbolo, tradare lo **spread** tra due simboli correlati (long uno, short l'altro) quando devia dalla sua media, scommettendo sul ritorno alla media. Market-neutral: il beta del mercato si cancella tra le due gambe.
- **Evidenza (misurata questa sessione)**: gli spread mean-revertono con half-life 9-34 giorni. Sketch di backtest z-score (rolling 200h, entry |z|>2, exit |z|<0,5): **BTC-XRP Sharpe 1,66, DOGE-XRP 1,18, BTC-ETH 1,23** al netto stimato; 3 coppie su 5 positive. Confronto: la strategia direzionale ha walk-forward totale negativo. **È l'unica alternativa con un segnale positivo già in mano.**
- **Perché attacca i limiti giusti**: sfrutta la correlazione (limite #2) invece di subirla; market-neutral elimina l'esposizione al crollo correlato (limite #5); non dipende dalla scarsità di segnali direzionali (limite #1).
- **Sforzo**: medio-alto. Nuovo layer di segnale (selezione coppie, hedge ratio, z-score), nuova semantica di posizione a due gambe nel backtester. **L'infrastruttura di rischio/ops si riusa quasi tutta** (vedi §5).
- **Rischi/tradeoff**: (a) half-life lunga = hold di giorni/settimane, profilo operativo diverso (position trading, non intraday); (b) non tutte le coppie funzionano (BTC-DOGE perde) → la selezione coppie va walk-forward-validata, prona a overfitting; (c) i numeri sono grezzi e non ancora walk-forward; (d) rischio di rottura della cointegrazione (una coppia può "divorziare" strutturalmente — serve uno stop sulla divergenza).

### B. Regressione invece di classificazione
- **Idea**: prevedere il rendimento atteso continuo invece di 3 classi, dimensionando la posizione sulla magnitudine.
- **Evidenza/prior**: nessuna misura diretta; potrebbe estrarre più informazione dal segnale esistente ma NON attacca scarsità/correlazione/regime — resta direzionale.
- **Sforzo**: medio (cambia model_fit, la policy, il target). **Rischio**: basso beneficio atteso dato che il problema non è la granularità della predizione ma la sua rarità. **Priorità bassa.**

### C. Modelli per-simbolo o regime-switching
- **Idea**: un modello per simbolo, o modelli separati trend/range con selezione a runtime.
- **Evidenza/prior**: sfavorevole. Per-simbolo aggrava la scarsità dati (ogni modello vede 1/7 della storia). Regime-switching: le feature di regime non erano nemmeno informative (#1 fallito), quindi il *selettore* di regime sarebbe altrettanto debole. **Priorità bassa.**

### D. Nuove fonti dati (order book, funding, open interest)
- **Idea**: feature di microstruttura (imbalance del book, funding rate, variazione di OI) — informazione che le candele 1h non contengono.
- **Evidenza/prior**: teoricamente il modo "giusto" per aumentare l'alpha (nuova informazione, non nuove trasformazioni della stessa). MA richiede **nuova infrastruttura di raccolta dati** (stream book/funding, storage, allineamento) — è il cambiamento più invasivo e più lontano dal vincolo "incrementale". **Priorità media, orizzonte lungo.**

### E. Modelli a sequenza (LSTM/temporal)
- **Idea**: rete che consuma la serie grezza invece delle feature hand-crafted.
- **Evidenza/prior**: sfavorevole per questo dataset. Con ~43k righe e segnale rado, i modelli a sequenza overfittano peggio di XGBoost; aggiungono complessità operativa (GPU, tuning) contro il vincolo di semplicità. **Priorità bassa.**

### F. Mean-reversion a singolo asset
- **Idea**: invertire la logica — comprare la debolezza, vendere la forza su un singolo simbolo.
- **Evidenza/prior**: non misurato direttamente, ma il successo dei pair (che sono mean-reversion su spread) suggerisce che la reversione esista; su singolo asset però resta esposto al beta. Meno interessante di A che è market-neutral. **Priorità media, come variante di A.**

---

## 4. Raccomandazione e piano a fasi

**Perseguire A (market-neutral stat-arb) come nuovo layer di segnale**, con un percorso a gate che può essere fermato a ogni fase se l'evidenza non regge — lo stesso rigore data-driven applicato finora.

- **Fase 0 — Validazione onesta del segnale (prima di scrivere codice di produzione, ~1-2 g).** Portare lo sketch a dignità di backtest: z-score con hedge ratio rolling (no look-ahead), fee/slippage reali sulle due gambe, **walk-forward** sulla selezione coppie (le coppie si scelgono su train, si tradano su test — mai in-sample). Metrica di gate: Sharpe walk-forward > 1,0 su un paniere di coppie, e comportamento nel fold "tossico" 2 (deve restare neutro, non affondare). **Se fallisce qui, si ferma — costo minimo.**
- **Fase 1 — Backtester a due gambe.** Estendere `backtest_joint` (o un fratello `backtest_pairs`) con posizioni a coppia: due gambe simultanee, margine condiviso già presente, fee doppie. Riusa fee/slippage/equity/drawdown/circuit-breaker.
- **Fase 2 — Layer di segnale di produzione.** Modulo `spread_signal` parallelo a `signal_policy`, con selezione coppie periodica (nel retraining settimanale). L'engine apre/chiude coppie invece di posizioni singole; gran parte della gestione ordini/posizioni si adatta, non si riscrive.
- **Fase 3 — Rischio specifico.** Stop sulla divergenza (cointegrazione rotta), limite sul numero di coppie aperte, interazione col circuit breaker esistente.

Il gate di Fase 0 è la decisione chiave: **non si tocca l'architettura finché il segnale non passa un walk-forward onesto.**

---

## 5. Cosa si preserva (il pivot è nel segnale, non nel sistema)

Tutta l'infrastruttura costruita nei punti 1-17 + N1-N5 è **agnostica alla strategia** e si riusa:

- **Rischio**: circuit breaker, cap di margine, cap direzionale, sizing, capitale persistente.
- **Validazione**: `backtest_joint` (fee/slippage/equity/DD), walk-forward, promozione multi-fold, model-health monitor.
- **Operatività**: WebSocket/candle feed con guardia dati-stantii, Redis listener robusto, watchdog (heartbeat/tick/Ollama/modello), systemd, Docker, dashboard.
- **Dati**: pipeline di download/refresh incrementale, store SQLite, calibrazione.

Il cambiamento è confinato al **generatore di segnali** (da "classifica la direzione di un simbolo" a "misura la deviazione di uno spread") e alla **semantica di posizione** (da singola a coppia). Stimo che il 70-80% del codice esistente resti valido. Questo rende il "cambio di architettura" molto meno traumatico di quanto il termine suggerisca — ed è la ragione per cui vale la pena valutarlo seriamente invece di continuare a spremere un modello direzionale al suo soffitto.

---

## 6. Onestà sui limiti di questa valutazione

- Lo sketch pairs è un backtest **grezzo, singola finestra, non walk-forward**: i Sharpe 1,2-1,7 sono un indizio promettente, non una promessa. La Fase 0 esiste apposta per confermarli o smentirli.
- 2 coppie su 5 non funzionano: la selezione coppie è essa stessa un problema di overfitting da trattare con lo stesso rigore usato per le soglie.
- La half-life lunga (giorni) cambia il profilo operativo del sistema — va valutato se è compatibile con gli obiettivi (finora il sistema mirava a hold di ore).
- Le alternative B-F sono valutate su prior e diagnosi, non su misure dedicate: se si volesse escluderle con certezza servirebbe un test mirato per ciascuna (ma i prior sono sfavorevoli e A è chiaramente il candidato più forte).
