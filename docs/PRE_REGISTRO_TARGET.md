# Pre-registro — Ricerca sulla definizione del target

**Data**: 2026-07-16 · **Stato**: DA APPROVARE · **Budget dichiarato**: 48 configurazioni

---

## Perché questo documento esiste

Dichiara **prima** di girare: cosa cerchiamo, quante volte guardiamo, e quale
risultato accetteremo come prova. Non è burocrazia — è l'unica cosa che rende
interpretabile ciò che troveremo.

Il 2026-07-16 una ricerca non pre-registrata ha prodotto un candidato
apparentemente ottimo (soglia 0.50: +244.65, 880 trade, 4/4 fold positivi).
Ricostruendo il conteggio dei tentativi a posteriori — 41 — il suo Deflated
Sharpe è risultato **21.4%**: sotto il livello che la fortuna produce da sola.
Lo stesso candidato dichiarando un solo tentativo avrebbe dato **97.3%**, cioè
"promuovere subito".

Il conteggio dei tentativi non è ricostruibile onestamente dopo, perché dopo si
conosce già la risposta. Va fissato prima. Questo documento lo fissa.

---

## Il problema

Il target — cioè **la domanda che poniamo al modello** — è quattro righe in
`src/training/feature_engine.py`:

```python
future_return = df['close'].shift(-5) / df['close'] - 1
target = FLAT
target[future_return >  0.005] = UP
target[future_return < -0.005] = DOWN
```

*"Fra 5 ore il prezzo sarà più dello 0.5% sopra o sotto?"*

Tutto il resto del sistema (18 feature, XGBoost, calibrazione di Platt,
walk-forward, soglie, uscite ATR) è **macchinario per rispondere a questa
domanda**. Era un paradigma di prova iniziale; l'architettura è cresciuta sopra
di esso e non è mai stato riesaminato. È l'unica scelta del sistema mai messa
alla prova, ed è alla base di tutte le altre.

### Perché sospettarlo

**S1 — La soglia è fissa, la volatilità no.** Nei fold del 2026-07-16 la
volatilità annualizzata andava da 0.40 a 0.78. Con soglia fissa, in regime
calmo lo 0.5%/5h è un evento raro (il modello impara "non succede nulla"), in
regime agitato è rumore di fondo (il modello impara a predire rumore). La
stessa costante pone domande diverse in periodi diversi, e noi le trattiamo
come se fossero la stessa.

**S2 — L'orizzonte è accoppiato per caso.** `TARGET_HORIZON_BARS = 5` e
`max_holding = 5` sono lo stesso numero senza che sia mai stato deciso. L'81%
dei trade chiude a MAX_HOLDING (media 4.39 barre su 5): la strategia *è* "tieni
esattamente per l'orizzonte del target". Se la prevedibilità reale stesse a 2 o
a 20 barre, non lo sapremmo.

**S3 — Il target ignora il percorso.** "+0.5% alla barra 5" non dice se nel
mezzo è sceso del 3%. Per il target è una vittoria; per lo stop loss è una
perdita realizzata. **Target e regola di trading non concordano su cosa sia una
vincita.**

**S4 — Il target non conosce il costo.** 0.5% di movimento contro 0.14% di
costo round-trip: insegniamo al modello a predire mosse che a malapena pagano
il biglietto.

---

## Ipotesi

Falsificabili, dichiarate prima:

- **H1** — Un target scalato sulla volatilità (k·ATR invece di 0.5% fisso)
  produce un segnale più forte di uno fisso, perché pone la stessa domanda in
  tutti i regimi. *Falsificata se:* nessuna variante ATR batte la migliore
  variante fissa.
- **H2** — L'orizzonte ottimale non è 5 barre. *Falsificata se:* 5 barre è il
  migliore o indistinguibile dal migliore.
- **H3** — Un'etichetta triple-barrier (quale barriera arriva prima: TP, SL, o
  tempo scaduto) converte meglio in PnL di una a orizzonte fisso, perché
  descrive ciò che accade davvero a un trade. *Falsificata se:* non batte
  l'orizzonte fisso a parità di orizzonte e soglia.
- **H4 (l'ipotesi nulla, la più probabile)** — Nessuna definizione di target
  supera la soglia di significatività. Il segnale non c'è, o non è accessibile
  con queste feature. *Questo è un risultato valido e va riportato come tale.*

---

## Spazio di ricerca — 48 configurazioni, dichiarate

| Dimensione | Valori | N |
|---|---|---|
| Orizzonte (barre) | 2, 5, 10, 20 | 4 |
| Soglia | fissa: 0.3%, 0.5%, 1.0% · ATR: 0.5×, 1.0×, 1.5× | 6 |
| Etichetta | orizzonte fisso, triple barrier | 2 |

**4 × 6 × 2 = 48.** Nessuna aggiunta a run iniziato: allargare lo spazio dopo
aver visto i primi risultati è il modo in cui il budget perde significato, ed è
esattamente ciò che è successo il 2026-07-16.

`max_holding` viene posto **uguale all'orizzonte** in ogni configurazione: è
l'accoppiamento che oggi esiste per caso (S2), qui reso deliberato e coerente.
Per il triple barrier, le barriere TP/SL coincidono con quelle del backtester
(3×ATR): è il punto dell'ipotesi H3 — target e regola di trading diventano la
stessa cosa per costruzione.

---

## Cosa resta costante (per non confondere le cause)

- **Feature**: le 18 con order flow. Immutate.
- **Modello**: XGBoost + calibrazione di Platt + early stopping. Nessun tuning
  di iperparametri — sarebbe un'altra ricerca su manopole, e brucerebbe budget
  dove il guadagno è piccolo per costruzione.
- **Walk-forward**: 4 fold, stessi confini (test 2023-01 → 2026-07).
- **Simboli**: i 7 operativi. Lo screening simboli è già bruciato.
- **Costi**: taker fee + slippage come da `config/trading_params.yaml`.
- **Soglia di probabilità**: **0.50**, fissa. Giustificazione *strutturale* e
  indipendente dal +244: sotto 0.50 il modello può emettere simultaneamente un
  segnale up e uno down sulla stessa barra (entrambe le probabilità possono
  superare la soglia); da 0.50 in su sono mutuamente esclusive per costruzione.
  È un confine matematico, non un valore tarato. Essendo costante non consuma
  tentativi.
- **Uscite SL/TP**: 3×ATR per le varianti a orizzonte fisso. Dimostrato
  irrilevante il 2026-07-16 (81% MAX_HOLDING, sweep su SL 2/3/5/8/20 e TP
  2/3/5/8/20 tutti piatti entro ±35 USDT).

---

## Criterio di successo — dichiarato prima di guardare

Una configurazione è **promuovibile** solo se supera **tutti** i punti:

1. **DSR > 90%** con `n_trials = 48` (il budget intero, non i tentativi fino a
   quel punto). La soglia in Sharpe/trade dipende dal numero di trade prodotti,
   perché più scommesse indipendenti = più evidenza:

   | trade prodotti | soglia-fortuna | Sharpe/trade richiesto | vs attuale (0.0651) |
   |---|---|---|---|
   | 400 | 0.1391 | **0.2033** | 3.1× |
   | 880 | 0.0938 | **0.1370** | 2.1× |
   | 2000 | 0.0622 | **0.0909** | 1.4× |

   Non è una scappatoia per i target che tradano molto: è la Legge Fondamentale
   della Gestione Attiva (IR ≈ IC × √breadth). Più scommesse indipendenti
   producono davvero più evidenza.

2. **Tutti e 4 i fold positivi.**
3. **Gate di robustezza superati** (sotto).
4. **Nessun simbolo contribuisce > 60% del PnL.** Il candidato del 2026-07-16
   aveva DOGE+SOL all'87%.

Se nessuna configurazione passa: **H4 confermata**, si riporta il negativo e
non si tocca l'holdout. Un holdout speso su un candidato che non ha superato il
gate è una cartuccia buttata.

---

## Gate di robustezza — automatici, non a posteriori

Il 2026-07-16 questi controlli hanno bocciato a mano un candidato che sembrava
vincente. Qui girano da soli, su ogni configurazione:

- **Attribuzione per simbolo** *dentro* il run a 7 (non leave-one-out: quello è
  confuso, perché il modello è addestrato sui dati aggregati e togliere un
  simbolo riaddestra tutto — il 2026-07-16 dava BTC a +8.56 di contributo ma la
  sua rimozione faceva crollare il totale da +244 a +23).
- **Perturbazione delle finestre**: stesso test a 6 fold. Il candidato di oggi
  passava da +244.65 (4/4) a +106.88 (4/6, peggiore −51.91).
- **Bootstrap a blocchi mensili**: IC 95% del PnL totale. Se include lo zero, è
  segnalato.

---

## Uso dell'holdout

- **Non toccato durante la ricerca.** `assert_research_allowed()` lo impedisce
  meccanicamente.
- Se e solo se **una** configurazione supera tutti i criteri: si congela, si
  apre il **lotto A** (BCH, ETC, ZEC, VET, THETA, SUSHI) con
  `open_seal("A", ipotesi, n_trials=48, ...)`, un solo test.
- Il **lotto B** (EOS, XLM, IOTA, NEO, ALGO, AAVE) resta sigillato per un
  candidato futuro. Se ne esistono due che passano, se ne sceglie **uno solo**
  a priori — testarli entrambi sul lotto A sarebbe una selezione, e riporterebbe
  il problema da capo.
- Attenzione leggendo il lotto B: EOSUSDT è delistato dal 2025-05-21. È tenuto
  apposta (un holdout di soli sopravvissuti avrebbe survivorship bias).

---

## Cosa NON faremo

- Tarare SL/TP/filtri: piatti e dimostrati irrilevanti.
- Tarare iperparametri del modello: guadagno piccolo per costruzione, quindi
  invisibile sotto la soglia-fortuna.
- Screening di simboli: bruciato.
- Aggiungere configurazioni a run iniziato.
- Riportare il migliore senza deflazione.
- Aprire l'holdout se nessuno supera i criteri.

---

## Registro

Ogni configurazione — vincente o perdente — viene iscritta in
`docs/experiment_registry.jsonl` sotto la famiglia `target_definition_v1` via
`record_trial()`. Le perdenti contano quanto le vincenti: senza il totale, il
DSR non è calcolabile e la correzione non corregge nulla.

**Conteggio a oggi**: 41 tentativi (`parametri_trading_5.8y_wf4fold`) +
2 (`order_flow_prereg`) = 43. Questa ricerca ne aggiunge 48.

---

## Esito — 2026-07-17, 7 minuti di calcolo

- **Configurazioni girate**: 48 / 48
- **Promuovibili**: **0**
- **DSR massimo osservato**: **29.8%** (h20, 0.5% fisso, orizzonte fisso) — molto
  sotto il 90% richiesto. Nessuna configurazione si è avvicinata.
- **Holdout**: **NON aperto**. Entrambi i lotti restano sigillati.

Risultati completi in `docs/target_search_results.csv`, tutti i 48 tentativi in
`docs/experiment_registry.jsonl` sotto `target_definition_v1`.

### H1 — soglia scalata su ATR: **FALSIFICATA**

Nessuna variante ATR batte la migliore variante fissa; sono quasi tutte
negative o attorno allo zero, e diverse tradano pochissimo (38-93 trade su 4.7
anni). L'ipotesi era mia ed era ragionevole — porre la stessa domanda in tutti
i regimi — ma i dati la rifiutano.

### H2 — l'orizzonte ottimale non è 5: **INDICATIVA, NON CONCLUSIVA**

I tre migliori per DSR sono tutti a soglia 0.5% fissa e orizzonte fisso:

| config | PnL | trade | fold+ | Sharpe/trade | DSR |
|---|---|---|---|---|---|
| h20 | +401.32 | 967 | 3/4 | 0.0726 | 29.8% |
| **h5 (produzione)** | +244.65 | 880 | 4/4 | 0.0651 | 19.8% |
| h10 | +245.77 | 778 | 3/4 | 0.0594 | 12.9% |

L'orizzonte 20 rende di più del 5 ma con un fold negativo e un DSR comunque
lontanissimo dalla soglia. Non è promuovibile e non giustifica una modifica.

### H3 — triple barrier: **NON TESTATA** (braccio confuso, errore di disegno)

Tutte e 24 le configurazioni triple barrier sono negative (PnL medio −356.9,
0 positive), con 2028 trade medi contro i 587 dell'orizzonte fisso. Non è una
falsificazione: è un **confondimento causato da questo pre-registro**.

Distribuzione delle classi (BTCUSDT, storia intera):

| config | DOWN | FLAT | UP |
|---|---|---|---|
| h5_0.005pct_fh | 25.3% | **47.9%** | 26.8% |
| h5_0.005pct_tb | 40.4% | **19.7%** | 39.9% |
| h10_0.005pct_tb | 44.3% | **11.8%** | 43.9% |

Con barriere strette quasi ogni barra ne tocca una, e la classe FLAT — il "non
fare nulla" — collassa. Le probabilità a priori di UP/DOWN salgono da ~26% a
~44%, quindi la soglia **fissa** a 0.50 è un filtro molto più permissivo per il
triple barrier che per l'orizzonte fisso. Da qui i 2028 trade e l'emorragia di
costi.

Fissare la soglia a 0.50 aveva una giustificazione strutturale valida (mutua
esclusività), ma non prevedeva che cambiare l'etichetta cambia le priorità
delle classi, e quindi cosa quella soglia *significhi*. **Il confronto non era
alla pari.** Metà del budget è stata spesa su un braccio ininterpretabile.

### H4 — nessun target supera la soglia: **CONFERMATA** (per il braccio testato)

Ventiquattro modi diversi di porre la domanda a orizzonte fisso, e nessuno
produce un segnale che batta la fortuna. Con il braccio H3 non valutabile, la
conclusione onesta non è "il target non è il problema", ma: **la soglia e
l'orizzonte del target non sono il problema.**

### Bug trovato dopo il run

`quota_simbolo_top` era `max/somma_netta`: con attribuzioni di segno misto
(+100 e −90 → 100/10) produceva quote fino al **3420%**. Corretta in
`max/profitto_lordo`, con test. **Non ha cambiato alcuna decisione**: il
criterio vincolante era il DSR, e con un massimo del 29.8% nessuna
configurazione è mai arrivata vicino al gate di concentrazione.

### Conteggio tentativi aggiornato

41 (`parametri_trading_5.8y_wf4fold`) + 2 (`order_flow_prereg`) + 48
(`target_definition_v1`) = **91**.

### Cosa NON fare adesso

Rilanciare il braccio triple barrier con una soglia calibrata per configurazione
sarebbe la correzione ovvia, ed è probabilmente giusta — ma è **una nuova
ricerca**, con un nuovo pre-registro e un budget che si somma ai 91 tentativi
già spesi. Non va infilata come "aggiustamento" di questa: allargare lo spazio
dopo aver visto i risultati è esattamente il meccanismo che il pre-registro
esiste per impedire.
