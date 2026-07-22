# Pre-registro — Chiusura della dimensione feature (funding + bookDepth)

**Data**: 2026-07-17 · **Stato**: DA APPROVARE · **Budget dichiarato**: 8 run

---

## Scopo

Chiudere **per misura** le ultime due famiglie di dati gratuiti con storia
utile, dopo l'esito nullo del positioning. Al termine, qualunque sia l'esito,
la dimensione feature sarà chiusa senza asterischi: ogni famiglia disponibile
sarà stata testata o dichiarata inaccessibile (i dati oltre queste si comprano
o si registrano da oggi).

Disponibilità verificata su S3 il 2026-07-17 **su più simboli, non uno solo**
(lezione della finestra metrics, dove la generalizzazione da BTCUSDT costò un
gate mancato):

- **fundingRate** (dump mensili): dal mese di quotazione di ogni simbolo
  (BTC 2020-01, ZRX 2020-06, 1INCH 2020-12, KAVA 2020-07). Copre l'intera
  finestra candele. Colonne: `calc_time, funding_interval_hours,
  last_funding_rate`.
- **bookDepth** (dump giornalieri): dal **2023-01-01 uniformemente** (4/4
  simboli sondati). Snapshot ~ogni 25 s, notional a ±1/2/3/4/5% dal mid.
  Finestra attesa ~3.55 anni.

## Il prior di chi scrive, dichiarato prima

**H-nulla attesa per entrambe, con prior più basso del positioning** (che è
appena uscito nullo):

- *Funding*: è lento (un evento ogni 8 ore), pubblico, ed è il numero più
  guardato dell'ecosistema perp. Che contenga segnale residuo a orizzonte
  5-10 barre orarie è improbabile.
- *bookDepth*: informazione di microstruttura vera (imbalance), il prior
  meno negativo dei due — ma la finestra è corta (3.55 anni, di cui il 40%
  bruciato in train), e la profondità a ±1% su un orizzonte di 5-10 **ore** è
  molto più lontana dal suo habitat naturale (secondi/minuti) di quanto l'OI
  fosse dal suo.

Questi esperimenti si fanno per **chiudere**, non perché ci si aspetti di
trovare. Un H-nulla qui è il completamento della frase "chiuso per misura".

---

## Disegno — due chiusure appaiate, autonome

Stesso schema del positioning: confronto appaiato baseline/arricchito, 2
orizzonti (h10 primaria, h5 controllo), q=1%, U47, walk-forward 4 fold,
`n_trials = 139` (131 a registro + 8 nuovi) per il DSR di ogni run.

Ogni chiusura misura **i propri baseline sulla propria finestra** — niente
riuso dei baseline positioning: finestre diverse renderebbero il confronto
non appaiato, e il riuso farebbe risparmiare 4 run al prezzo della pulizia.

### Famiglia `funding_v1` (4 run) — finestra attesa ~5.5 anni

Feature dichiarate (due, nessuna variante):

1. `funding_last` = ultimo `last_funding_rate` con `calc_time ≤` chiusura
   barra (già un tasso: scale-free per natura)
2. `funding_sum_9` = somma degli ultimi 9 funding noti (~3 giorni di
   pressione cumulata)

Allineamento asof all'indietro, tolleranza **9 ore** (un evento ogni 8: oltre,
il dato è perso e la riga è NaN, mai riempita). Gate: intersezione
candele∩funding ≥ **5.0 anni** sui 47, o STOP.

### Famiglia `bookdepth_v1` (4 run) — finestra attesa ~3.55 anni

All'ingestione si riducono gli snapshot (~25 s) all'**ultimo di ogni ora**:
l'orizzonte è orario e portare 45M righe/simbolo nel walk-forward non aggiunge
informazione, solo costo.

Feature dichiarate (due, nessuna variante):

1. `depth_imbalance_1` = (notional bid a −1% − notional ask a +1%) / somma —
   in [−1, +1], lo sbilancio del libro vicino al prezzo
2. `depth_total_ratio_20` = notional totale entro ±1% / propria media mobile
   20 barre — assottigliamento/ispessimento della liquidità

Allineamento asof all'indietro, tolleranza 2 ore. Stessa guardia anti-inf del
positioning (|x| < 1e6, altrimenti NaN). Gate: intersezione ≥ **3.0 anni**
sui 47, o STOP.

**Avvertenza dichiarata sulla finestra corta**: 3.55 anni × 40% di train
lasciano ~2.1 anni di test in 4 fold. La potenza è la più bassa di tutti gli
esperimenti della serie: un H-nulla qui chiude la pista *gratuita*, non
l'ipotesi microstrutturale in sé (che richiederebbe dati comprati o
registrati).

---

## Criteri — identici alla serie

- **Appaiato (per ciascuna famiglia)**: l'arricchito batte il baseline in
  Sharpe/trade in 2/2 coppie = indicativo; ≤1/2 = falsificata.
- **Promozione (solo h10+arricchito)**: DSR > 90% con n_trials=139 · 4/4 fold
  positivi · concentrazione ≤ 60% · **bootstrap mensile IC95 > 0 (gate
  primario)**.
- Holdout: invariato, apertura solo per un candidato che passi tutto.

## Registro

Famiglie `funding_v1` e `bookdepth_v1`. Ogni run, anche perdente, inclusi i
run di eventuali crash intermedi (come per positioning: il sovraconteggio è
conservativo). **Conteggio atteso a fine serie: 139.**

---

## Esito — 2026-07-17/18

- **Funding** — gate 5.51 anni ✅ · coppie vinte **1/2 → FALSIFICATA** ·
  primario h10+funding: DSR 0.0%, 1/4 fold, IC95 [−789, +321] → **H-nulla**
- **bookDepth** — gate 3.54 anni ✅ · coppie vinte **1/2 → FALSIFICATA** ·
  primario h10+bookdepth: DSR 0.0%, 1/4 fold, IC95 [−713, +208] → **H-nulla**
- **Holdout**: NON aperto. Lotti A e B sigillati.
- **Dimensione feature: CHIUSA senza asterischi gratuiti.**

| Famiglia | Config | Baseline SR | Arricchito SR | Vincitore |
|---|---|---|---|---|
| funding (5.51y) | h10 | −0.0614 | −0.0173 | funding |
| funding | h5 | −0.0340 | −0.0597 | baseline |
| bookdepth (3.54y) | h10 | −0.0399 | −0.0361 | bookdepth |
| bookdepth | h5 | −0.0654 | −0.0912 | baseline |

Il pattern è identico in tutte e tre le chiusure (positioning, funding,
bookdepth): l'arricchito vince su h10, perde su h5, **1/2 = monetina**, e
tutti i bracci sono negativi. Tre famiglie di informazione ortogonale al
prezzo — posizionamento, funding, microstruttura — e nessuna crea un edge
dove il prezzo non ne trovava. Nota: entrambi i baseline h5 di questa serie
hanno IC95 interamente negativo ([−766, −70] e [−814, −170]): a orizzonte
corto la strategia perde in modo statisticamente significativo, coerente con
"i costi dominano".

### Bilancio della dimensione feature (139 tentativi totali a registro)

Testate per misura: 18 OHLCV+flow (base) · 4 order flow (order_flow_prereg) ·
4 positioning (OI, long/short) · 2 funding · 2 bookDepth (±1%).
Inaccessibili gratis: liquidazioni storiche orarie (CoinGlass ≥$299 non
verificato), on-chain con API (≥$999/mese), order book tick-level (Tardis).

La frase finale della ricerca storica non cambia, ma ora copre tutto il
gratuito: **questi dati — prezzo, volume, flusso, posizionamento, funding,
profondità — non contengono un edge direzionale dimostrabile a orizzonte
orario con costi retail.**
