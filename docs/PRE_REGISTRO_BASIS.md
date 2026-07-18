# Pre-registro — Basis trimestrale (cash-and-carry su delivery futures)

**Data**: 2026-07-18 · **Stato**: DA APPROVARE · **Budget dichiarato**: 2 configurazioni

---

## Il gioco, e perché è a somma positiva

Long spot + short future **trimestrale con scadenza**: il future quota a premio
sullo spot quando c'è domanda di leva long, e **alla scadenza il premio va a
zero per costruzione** (il contratto regola sull'indice spot). Chi entra
incassa il basis di entrata in modo quasi deterministico: non è una
predizione, è la convergenza contrattuale. Chi paga è chi vuole leva long
senza toccare lo spot — paga volentieri, per un servizio.

È il fratello a termine del carry sul funding (promosso, `carry_v1`), con una
differenza strutturale: **il rendimento è bloccato all'entrata** (si legge il
basis annualizzato e si sa cosa si incasserà tenendo fino a scadenza),
mentre il funding fluttua ogni 8 ore. Letteratura: carry medio storico ~8.7%
su BTC (BIS WP 1087).

## Dati — verificati su S3 prima di scrivere (2026-07-18)

- **USDT-M delivery**: esistono SOLO `BTCUSDT_YYMMDD` ed `ETHUSDT_YYMMDD` —
  24 contratti trimestrali ciascuno dal 2021-03, di cui **22 già scaduti**
  (≈5.5 anni di storia). Klines nei dump mensili.
- Spot BTC/ETH: già in casa (`data/spot/`).
- **COIN-M escluso per due ragioni dichiarate**: (1) tre sottostanti (BCH,
  EOS, ETC) sono **asset dell'holdout sigillato** — il guardiano formale non
  li bloccherebbe (`BCHUSD` ≠ `BCHUSDT`) ma usarne la storia prezzi
  violerebbe lo spirito del sigillo; (2) contratti inversi = contabilità di
  margine diversa da riscrivere e testare. Eventuale estensione = nuovo
  pre-registro.

**Gate**: ≥ 20 trimestri chiusi per entrambi i sottostanti, o STOP.

## Il prior di chi scrive, dichiarato prima

**Positivo lordo quasi per costruzione, netto atteso modesto e in
compressione** — stessa dinamica del funding: la media storica include il
2021 euforico; il basis recente è compresso dall'afflusso di capitale
istituzionale (ETF, desk cash-and-carry). Mi aspetto un esito "promosso sui
5.5 anni, magro nel regime recente", gemello del carry. Se così sarà, il
valore dell'esperimento è avere **due misure indipendenti della stessa
compressione** — funding e basis sono la stessa domanda di leva vista da due
strumenti.

## Strategia — regole meccaniche

A ogni scadenza trimestrale (venerdì di regolamento), sul contratto
successivo (~90 giorni):

- **Config 1 — `always-roll`** (**PRIMARIA**): entra sempre. Short future +
  spot già in portafoglio, tieni fino a scadenza, ripeti. La più passiva:
  nessuna decisione, nessun parametro.
- **Config 2 — `positive-only`**: entra solo se il basis annualizzato di
  entrata è > 0; altrimenti quel trimestre resta in cash (rendimento 0).

Portafoglio: **50/50 BTC/ETH**, ribilanciato a ogni scadenza. Serie dei
rendimenti trimestrali di portafoglio (~22 osservazioni).

## Contabilità — dichiarata prima

Per trimestre, in frazione del capitale (spot al prezzo di entrata):

```
rendimento = (F_entrata − S_entrata) / S_entrata     [il basis bloccato]
           − 0.07%  (entrata future: taker 0.05% + slippage 0.02%)
           − 0.05%  (regolamento a scadenza, prudenziale)
```

Lo spot si compra una volta all'inizio e si vende alla fine (0.07% × 2,
ammortizzati sull'intera storia). Il basis di entrata si legge dai close 1h
di future e spot al primo giorno utile dopo la scadenza precedente.

**Riga zero, dichiarata** (dal criterio-meta della lista dei giochi): la
strategia rende in dollari, quindi il confronto onesto è il **T-bill USA a
3 mesi** medio del periodo, riportato accanto al risultato. Un basis che non
batte il T-bill non paga il rischio exchange.

## Cosa il backtest NON cattura — dichiarato

- **Margine della gamba corta nei pump** (identico al carry): il netto è
  bloccato ma il margine del future va rifornito lungo la strada.
- **Rischio controparte/exchange** — più rilevante che mai: qui il confronto
  con il T-bill (rischio ~zero) è la riga zero proprio per questo.
- **Liquidità dei trimestrali**: più sottile dei perpetual; lo slippage
  dichiarato (2 bps) è realistico per BTC/ETH, non generalizzabile.

## Criterio di successo — dichiarato prima di guardare

Sulla **primaria** (`always-roll`), serie dei rendimenti trimestrali netti.
Tutti in AND (struttura dell'opzione C, come per il carry):

1. **Bootstrap sui trimestri: IC 95% dell'annualizzato netto > 0** — gate
   primario.
2. **DSR > 90% con `n_trials = 2`** (questa famiglia).
3. **Netto annualizzato ≥ +3%.**
4. **Trimestri positivi ≥ 55%.**
5. **Nessun sottostante oltre il 70% del rendimento totale** (con 2 asset la
   soglia di concentrazione del carry non ha senso; 70% = "non è solo BTC").

DSR cumulativo (`n_trials = 145`) riportato, non vincolante — stessa
motivazione a verbale dell'opzione C del carry.

**Spaccato annuale riportato sempre** (lezione del carry): la media 5.5 anni
e il regime recente sono due numeri diversi e vanno mostrati entrambi.

## Registro

Famiglia `basis_v1`. **Conteggio: 143 + 2 = 145.**

---

## Esito — 2026-07-18

- **Gate**: 22 contratti scaduti per sottostante ✅ (23 trimestri di portafoglio)
- **Primaria (always-roll)**: +4.81% netto ann. · IC95 **[−0.90%, +9.16%]**
  (include lo zero) · DSR₂ 67.2% · trimestri+ 91% · conc. 59%
- **Riga zero**: T-bill 3M medio del periodo **+3.41%** → eccesso **+1.40%**
- **H9-nulla: NON promuovibile.** 2 criteri su 5 falliti (bootstrap e DSR).

| Config | ann. netto | trim+ | IC95 | DSR₂ |
|---|---|---|---|---|
| **always-roll** (primaria) | +4.81% | 91% | **[−0.9%, +9.2%]** | 67.2% |
| positive-only | +6.75% | 96% | [+4.2%, +9.6%] | 100% |

Spaccato annuale della primaria: 2021 +11.55% · 2022 +1.63% · 2023 +7.30% ·
2024 +13.19% · 2025 +4.15% · **2026 −10.12%** (3 trimestri).

### Le due informazioni nuove

**1. Nel 2026 il basis si è invertito (backwardation).** Non "compresso" come
il funding: **negativo**. L'always-roll ha shortato future sotto lo spot,
bloccando perdite per −10.12% in tre trimestri. È un'informazione di regime
più forte di quella del carry: la domanda di leva long, vista dallo strumento
a termine, non è solo magra — nel 2026 ha cambiato segno.

**2. L'eccesso sul T-bill è +1.40% sull'intera storia.** La riga zero fa il
suo lavoro: anche nella media 5.5 anni (che include il 2021 euforico), il
premio per il rischio exchange/margine rispetto al tasso privo di rischio è
sottile. Nel regime recente è negativo.

### La nota metodologica — e la disciplina che si applica

La `positive-only` **passa numericamente tutti i criteri** (IC95 [+4.2%,
+9.6%], DSR₂ 100%). Ed era pre-dichiarata, con una regola che usa solo
informazione disponibile all'entrata (il basis si legge prima di entrare: non
è predizione). **Ma la promozione era dichiarata sulla sola primaria, e la
primaria è bocciata.** Promuovere ora la secondaria perché "è andata meglio"
sarebbe selezione a posteriori — la mossa che l'impianto vieta; e
ri-registrarla come nuova primaria sugli stessi dati, dopo averne visto
l'esito, sarebbe la stessa mossa con un timbro sopra.

Ciò che la positive-only *dice* legittimamente, come descrittivo: il
meccanismo di convergenza funziona quando il basis d'entrata è positivo (96%
di trimestri positivi) — coerente con la natura contrattuale del gioco.
Un'eventuale decisione operativa su di essa richiederebbe **validazione in
avanti** (paper trading), non un altro giro sugli stessi 23 trimestri.

### Conferma del prior

"Gemello del carry" confermato: forte 2021/2024, magro 2025, negativo 2026.
Funding e basis — la stessa domanda di leva vista da due strumenti — ora
raccontano la stessa storia con **due misure indipendenti**: il premio che i
long a leva pagavano nel 2021-2024 si è compresso fino, nel 2026, a
invertirsi.

## Registro

`basis_v1`: 2 tentativi. **Totale: 145.**