# Pre-registro — Timeframe

**Data**: 2026-07-17 · **Stato**: DA APPROVARE · **Budget dichiarato**: 5 configurazioni

---

## Perché esiste

La conclusione raggiunta dopo 120 tentativi è: *"questi dati non contengono un
edge dimostrabile **a 1h** con queste feature"*. È **scoped a 1h**, e il lato
lungo di quello scope è stato chiuso **per inferenza, non per misura**: avevo
dedotto che timeframe più lunghi dessero meno potenza, a universo fisso.

A universo fisso era vero. Con i 47 simboli è **falso**, e le due leve si
compongono:

| tf | barre/simbolo | barre × 47 | mossa 5 barre | mossa/costo |
|---|---|---|---|---|
| 1h | 53.109 | 2.496.166 | 1.723% | 12.31× |
| 2h | 26.555 | 1.248.101 | 2.462% | 17.58× |
| **4h** | **13.278** | **624.087** | **3.544%** | **25.31×** |
| 8h | 6.640 | 312.080 | 4.977% | 35.55× |
| 1d | 2.214 | 104.061 | 8.907% | 63.62× |

> confronto: 1h × 7 simboli = **392.670 barre** — l'intero dataset su cui
> abbiamo lavorato finora.

**4h × 47 simboli dà 624.087 barre — 1.6× tutto ciò che abbiamo usato — con il
doppio del margine economico.** Più dati *e* economia migliore, insieme.

### Il meccanismo (perché non è una pescata)

**Il costo è fisso a 0.14% per trade. Il movimento cresce con √tempo.**

Se il modello cattura una *frazione costante* del movimento — e l'analisi dei
bucket del 2026-07-16 dice che ne cattura una reale (+2.05% netto a P>0.60) —
allora il timeframe lungo converte meglio **per pura aritmetica**, senza che il
modello migliori di una virgola. A 1h il costo si mangia l'8.1% del movimento
tipico; a 4h il 4.0%.

È il muro dei 120 tentativi visto dall'altro lato: **non abbiamo mai provato a
cambiare il costo relativo invece del segnale.**

### Il lato corto è già chiuso, per misura

A 5m il movimento copre il costo **1.76×** (misurato 2026-07-17). Andare più
corti è aritmeticamente perdente. Fuori dallo spazio.

---

## Il prior di chi scrive: negativo

Va messo per iscritto **prima**, così l'esito non è riscrivibile a posteriori.

**Ricampionare non aggiunge informazione**: 4h sono gli stessi prezzi di 1h,
aggregati. E c'è un indizio contrario già in casa: nella ricerca sul target
`h20@1h` — orizzonte di **20 ore**, lo stesso di `h5@4h` — era la
configurazione migliore di tutte, e dava DSR **29.8%**. Orizzonti lunghi
aiutano un po'; "un po'" non basta.

La sola differenza reale fra `h20@1h` e `h5@4h`: a 4h le **feature** sono
calcolate su barre lisciate, quindi meno rumorose. Non è nulla, ma non è
informazione nuova.

**Mi aspetto un altro nulla.** Lo si fa lo stesso per tre motivi che non
c'entrano con la speranza:

1. La conclusione è scoped a 1h: chiuderla **per misura** invece che per
   inferenza è la disciplina applicata a tutto il resto — e il 2026-07-17
   l'inferenza al posto della misura ha prodotto quattro errori (sigillo su
   simboli già visti, lista senza UNIUSDT, parquet troncati a 1 anno, lato
   lungo del timeframe).
2. Costa quasi nulla: 5 tentativi (l'asticella passa da 3.11 a 3.13, invisibile),
   **nessun download**, minuti di calcolo.
3. È l'ultima dimensione mai misurata. Dopo, *"non c'è edge in questi dati"*
   sarà una frase guadagnata, non dedotta.

---

## Spazio di ricerca — 5 configurazioni

I timeframe si ottengono **per aggregazione esatta dai parquet 1h** — nessun
download, nessuna nuova fonte:
`open=first, high=max, low=min, close=last, volume/n_trades/taker_buy_base=sum`.

| # | Timeframe | Universo | Ruolo |
|---|---|---|---|
| 1 | 2h | 47 | curva |
| 2 | **4h** | **47** | **TEST PRIMARIO** |
| 3 | 8h | 47 | curva |
| 4 | 1d | 47 | curva |
| 5 | 4h | i nostri 7 | descrittivo (vedi sotto) |

**La decisione si prende solo su 4h × 47**, dichiarato ora. 4h è scelto perché
è **l'unico timeframe che dà simultaneamente più dati del nostro dataset
attuale** (624k > 393k barre) **e un margine di costo materialmente migliore**
(25.3× contro 12.3×). È una regola, non un risultato: 8h e 1d hanno economia
migliore ma meno dati di quelli su cui abbiamo lavorato finora.

La #5 (4h sui nostri 7) è **descrittiva e non promuovibile**: serve solo a
sapere se il passaggio 1h→4h aiuta il gruppo che sappiamo essere sovradattato.
Un suo eventuale successo **non è un candidato** — sarebbe la stessa illusione
già smontata due volte (+244, e Sharpe +0.119 contro −0.055 cambiando i sette
simboli).

### Cosa resta congelato

- **Orizzonte: 5 barre** per ogni timeframe (→ 10h, 20h, 40h, 5 giorni).
  `max_holding = 5` barre, accoppiato deliberatamente.
- **Soglia del target: 0.5% × √(ore per barra)** → 2h: 0.71% · 4h: 1.00% ·
  8h: 1.41% · 1d: 2.45%. La volatilità scala con √tempo: così il **tasso di
  eventi resta costante** fra i timeframe e il confronto è alla pari. È una
  regola derivata, **non una manopola**: non consuma tentativi. (È la lezione
  di H3, dove una soglia fissa su etichette diverse confrontava filtri invece
  che ipotesi.)
- **Etichetta**: orizzonte fisso (H3 ha falsificato il triple barrier 0/12,
  p=0.0005).
- **q = 1%**, feature (18 con order flow), modello, 4 fold, uscite 3×ATR.

### Trappola tecnica nota

`BARS_PER_YEAR_1H = 24*365` è **hardcoded** in `backtester.py`: il campo
`sharpe` di `BacktestResult` annualizza come se ogni barra fosse un'ora, e a 4h
sovrastima di √4 = **2×**.

**Non ci tocca**: le nostre metriche (`sharpe_trade`, DSR, bootstrap mensile)
sono calcolate in `src/research/evaluation.py` dai PnL dei **singoli trade**, e
sono indipendenti dal timeframe. Il campo sbagliato non viene letto. Scritto
qui perché chi legge i risultati non ci caschi, e perché il giorno che servisse
va corretto e non aggirato.

---

## Ipotesi

- **H6a — Il costo relativo è il vincolo.** A 4h × 47 lo Sharpe/trade è > 0.
  *Falsificata se:* resta ≤ 0 come a 1h (−0.0170). Se cade, il problema non era
  il costo: è che **il segnale non c'è**, e nessuna aritmetica lo crea.
- **H6b — La curva.** Lo Sharpe/trade cresce monotonamente 2h→1d, seguendo
  mossa/costo. *Falsificata se:* piatta o senza direzione.
- **H6c — Promuovibile.** 4h × 47 supera tutti i criteri sotto.
- **H6-nulla** — Nessun timeframe produce un edge dimostrabile. **Esito atteso.**
  Con target, feature, breadth e timeframe tutti chiusi **per misura**, la
  ricerca sui dati storici è conclusa e la frase perde ogni aggettivo: *questi
  dati non contengono un edge dimostrabile con queste feature.*

---

## Criterio di successo — dichiarato prima di guardare

1. **DSR > 90%** con **`n_trials = 125`** (120 spesi + 5 nuovi).
2. **Tutti e 4 i fold positivi.**
3. **Nessun simbolo oltre il 60% del profitto lordo.**
4. **Bootstrap a blocchi mensili: IC 95% che esclude lo zero.** **Gate
   primario**: con 47 cripto correlate i trade non sono indipendenti e il DSR —
   che li assume iid — sovrastima. **Se DSR e bootstrap dissentono, vince il
   bootstrap.**

Se 4h × 47 fallisce: **H6-nulla**, holdout non aperto, ricerca conclusa.

---

## Uso dell'holdout

Invariato. Lotti A e B sigillati, mai sfiorati in 120 tentativi. Apertura solo
se 4h × 47 supera tutto, come atto separato e deliberato, su un solo candidato.

---

## Registro

Famiglia `timeframe_v1` in `docs/experiment_registry.jsonl`. Ogni tentativo,
anche perdente.

**Conteggio**: 120 spesi + 5 = **125**.

---

## Esito — 2026-07-17, ~40 secondi di calcolo

- **Configurazioni girate**: 5 / 5
- **4h × 47 — Sharpe/trade: −0.0065** · 914 trade
- **H6a FALSIFICATA · H6b FALSIFICATA · H6c FALSIFICATA**
- **Holdout**: **NON aperto**. Lotti A e B sigillati.

| tf | universo | PnL | trade | fold+ | Sharpe/trade | soglia | IC95 bootstrap |
|---|---|---|---|---|---|---|---|
| 2h | 47 | — | 641 | — | −0.0084 | 0.71% | — |
| **4h** | **47** | **−44.87** | **914** | **3/4** | **−0.0065** | 1.00% | **[−561, +457]** |
| 8h | 47 | −95.19 | 506 | 2/4 | −0.0245 | 1.41% | [−525, +311] |
| 1d | 47 | −547.12 | 287 | 0/4 | **−0.3254** | 2.45% | [−821, **−290**] |
| 4h | **nostri 7** | +118.65 | 213 | 3/4 | **+0.0748** | 1.00% | [−122, +365] |

### H6a — FALSIFICATA. Il costo relativo non era il vincolo.

A 4h × 47 lo Sharpe/trade è **−0.0065**, contro −0.0170 a 1h. La direzione è
quella prevista dal meccanismo (raddoppiare il margine mossa/costo migliora
qualcosa) ma l'effetto è **microscopico e resta negativo**. Il costo non era il
tappo: **il segnale non c'è, e nessuna aritmetica lo crea.**

### H6b — FALSIFICATA. La curva non segue mossa/costo.

Se il costo relativo comandasse, lo Sharpe/trade dovrebbe crescere monotono
2h→1d insieme al margine (17.6× → 63.6×). Osservato:

> −0.0084 · **−0.0065** · −0.0245 · **−0.3254**

Migliora appena da 2h a 4h, poi **peggiora**, e a 1d collassa. Il margine di
costo cresce di 3.6× da 4h a 1d mentre lo Sharpe crolla di 50×: il meccanismo
esiste ma è dominato da qualcos'altro — a orizzonti lunghi il modello
semplicemente non predice.

### La terza conferma indipendente, ed è la stessa di sempre

| | Sharpe/trade |
|---|---|
| 1h × **i nostri 7** (h10, q=1%) | **+0.1190** |
| 1h × 7 simboli **a caso** | −0.0554 |
| 1h × **47** | −0.0170 |
| 4h × **i nostri 7** | **+0.0748** |
| 4h × **47** | −0.0065 |

Il disegno è completo e non ha eccezioni: **i nostri sette simboli danno sempre
positivo, tutto il resto sempre zero o negativo** — a qualunque timeframe, con
qualunque target, con qualunque ampiezza. Tre esperimenti indipendenti, tre
volte lo stesso verdetto.

Non è un edge che vive sui nostri 7. È il residuo di averli scelti una volta e
poi guardati 125 volte.

*(La riga `4h × nostri 7` è **descrittiva e non promuovibile**, come dichiarato
prima del run. È qui perché conferma la diagnosi, non perché sia un candidato.)*

---

## Conclusione — la ricerca sui dati storici è conclusa

Con **125 tentativi registrati** su 5.8 anni e 47 simboli, ogni dimensione è
chiusa **per misura, non per inferenza**:

| Dimensione | Esito | Come è stata chiusa |
|---|---|---|
| **Target** | chiuso | H1 soglia ATR falsificata · H2 orizzonte non conclusivo · H3 triple barrier 0/12 (p=0.0005) |
| **Feature** | chiuso | OI, long/short, order book: 20 giorni di ritenzione. L'order flow — l'unica aggiunta sostanziale — non ha spostato nulla |
| **Breadth** | chiuso | 3.3× i trade, IC ≈ 0. `IR ≈ IC × √breadth` non ha nulla da moltiplicare |
| **Timeframe** | chiuso | corto: mossa/costo 1.76× · lungo: H6a/H6b falsificate |

> **Questi dati non contengono un edge dimostrabile con queste feature.**
> Non "a 1h". Non "con questo target". Punto.

È un **risultato**, non una resa: è ciò che sappiamo dopo 125 tentativi
**contati**, con un metodo costruito per impedirci di ingannarci. Il 2026-07-16
"avevamo trovato" +244 con 4/4 fold positivi, ed era falso.

**L'holdout non è mai stato aperto**: due lotti da 6 simboli mai guardati,
entrambi carichi. Nessun candidato lo ha meritato in 125 tentativi. È
l'unica risorsa non consumata che resta, ed è intatta perché il sistema ha
funzionato — non per prudenza di qualcuno.
