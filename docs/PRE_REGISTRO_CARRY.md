# Pre-registro — Carry sul funding (delta-neutro, senza ML)

**Data**: 2026-07-18 · **Stato**: DA APPROVARE · **Budget dichiarato**: 4 configurazioni

---

## Il cambio di gioco

Tutti i 139 tentativi precedenti condividevano una premessa: **predire la
direzione del prezzo**. È la premessa che le misure hanno demolito: il gioco
direzionale intraday è a somma zero meno i costi, e i costi retail sono
l'avversario che vince.

Il carry è l'altro lato del tavolo. Short perpetual + long spot sullo stesso
simbolo = **delta zero**: il prezzo può fare qualunque cosa, la posizione non
lo sente. Ciò che incassa è il **funding** — il flusso che i long a leva
pagano agli short ogni 8 ore quando il funding è positivo. Non è una
scommessa: è vendere un servizio (assorbire la domanda di leva long).

**Misura fondante** (2026-07-17, sui dati funding già scaricati, 5.5 anni ×
47 simboli): funding medio annualizzato **mediana +8.0%, positivo su 41/47**.
Il flusso esiste ed è persistente. La domanda dell'esperimento è se
sopravvive a costi, rotazione e basis.

## Il prior di chi scrive, dichiarato prima

**Positivo lordo con alta confidenza, incerto netto.** Diversamente dai 139
precedenti, qui l'aspettativa lorda è positiva *per costruzione* (il flusso è
misurato, non predetto). I rischi sono: costi di rotazione (4 gambe per
ciclo), basis all'entrata/uscita, concentrazione temporale del funding (i
periodi ricchi sono i bull euforici — 2021, 2024 — e possono non tornare),
e compressione futura del rendimento (strategia nota a tutti). Un esito
"positivo ma sottile" è il più probabile; "negativo netto" è possibile se la
rotazione mangia il flusso.

---

## Strategia — regole meccaniche, nessun parametro appreso

Ogni lunedì 00:00 UTC (ribilanciamento settimanale):

1. Per ogni simbolo dell'universo, calcola il **funding medio degli ultimi
   `W` giorni** (solo eventi passati: nessun lookahead).
2. Seleziona i simboli col funding medio **> 0** (regola `all-positive`)
   oppure i **top-10** per funding medio (regola `top-10`).
3. Portafoglio equal-weight sui selezionati: per ciascuno, long spot 1×
   notional + short perp 1× notional.
4. Le posizioni già aperte e riselezionate **non si toccano** (niente costi);
   si chiudono le uscite e si aprono le entrate.

### Le 4 configurazioni

| # | Finestra W | Selezione | Ruolo |
|---|---|---|---|
| 1 | 30 giorni | all-positive | **PRIMARIA** (la più passiva, meno selezionata) |
| 2 | 30 giorni | top-10 | curva |
| 3 | 7 giorni | all-positive | curva |
| 4 | 7 giorni | top-10 | curva |

In più una riga **benchmark descrittiva, non a budget**: tenere sempre tutti
e 47 senza selezione (il flusso incondizionato — la misura fondante come
strategia).

## Contabilità del PnL — dichiarata prima

Per ogni posizione, dal momento dell'apertura alla chiusura:

```
PnL = Σ funding incassati sul notional perp
    − costi:   apertura 2 gambe (taker 0.05% + slippage 0.02% ciascuna)
             + chiusura 2 gambe                     = 0.28% del notional/ciclo
    + Δbasis:  (basis_chiusura − basis_apertura) × notional
               dove basis = (perp − spot) / spot, misurato sui close 1h
```

Il rendimento è quotato **sul notional**; la nota di capitale (spot pieno +
margine perp) è riportata a parte, non nascosta nel numero.

**Dati**: funding già in casa (5.51 anni × 47); klines perp 1h già in casa;
klines **spot 1h da scaricare** dai dump mensili (verificato su 6/6 simboli
sondati, storia dal 2017-2020). Regola d'universo: i 47 di sempre; un simbolo
senza mercato spot è escluso **per regola** (meccanica, non per rendimento).

**Gate**: intersezione (funding ∩ perp ∩ spot) ≥ **5.0 anni** su ≥ **40
simboli**, o STOP e diagnosi.

## Cosa il backtest NON può catturare — dichiarato

- **Rischio di margine sulla gamba perp**: in un pump violento lo short perp
  perde mark-to-market mentre lo spot guadagna; il netto è ~zero ma il
  margine del perp va rifornito. Si assume gestione cross-margin adeguata;
  dal vivo è un rischio operativo reale (liquidazione della gamba corta).
- **Rischio controparte/exchange** (tutto su Binance).
- **Compressione del rendimento**: il backtest misura il passato di una
  strategia che più capitale la adotta, meno rende.
- **Esecuzione**: slippage fisso 2 bps; su alt sottili nei momenti di stress
  è ottimistico.

## Criterio di successo — dichiarato prima di guardare

Sulla configurazione **primaria** (#1), rendimento **netto** mensile. Tutti e
cinque i criteri, in AND:

1. **Bootstrap sui mesi: IC 95% del rendimento annualizzato netto > 0** —
   gate primario, come sempre.
2. **DSR > 90% con `n_trials = 4`** (i tentativi di QUESTA famiglia) sui
   rendimenti mensili. Con 66 mesi equivale a richiedere **Sharpe
   annualizzato ≥ ~1.26**: boccia un carry mediocre, non uno reale.
3. **Rendimento annualizzato netto ≥ +3%** — sotto questa soglia il gioco non
   paga il rischio operativo non modellato (margine, controparte).
4. **Mesi positivi ≥ 55%.**
5. **Nessun simbolo oltre il 40% del funding incassato totale** (il flusso
   deve essere diffuso, non un simbolo anomalo).

Il DSR con conteggio **cumulativo** (`n_trials = 143`) è **riportato ma non
vincolante**, con la motivazione a verbale: il conteggio cumulativo corregge
la selezione fra configurazioni, e i 139 tentativi precedenti appartengono
all'esplorazione direzionale — il carry non è stato pescato fra quelli, è
un'ipotesi strutturale con aspettativa misurata prima del disegno. Con N=143
la soglia sarebbe Sharpe ~1.89, che boccia per costruzione qualunque carry
realistico (i fondi carry reali vivono fra 0.8 e 1.5): il test smetterebbe
di essere informativo. Resta però vera una selezione **al livello delle
ipotesi** (il carry è stato proposto dopo aver visto fallire il direzionale):
per questo il conteggio di famiglia N=4 è VINCOLANTE e il criterio non si
riduce al solo bootstrap. Decisione presa con l'utente il 2026-07-18, prima
di qualsiasi run.

Se la primaria fallisce e una secondaria passa: **non si promuove** (sarebbe
selezione), si riporta e basta.

## Holdout

Invariato: lotti A e B sigillati. Un'eventuale validazione del carry
sull'holdout richiederebbe funding+spot dei simboli sigillati: si farebbe
SOLO via `open_seal`, come atto separato, su un candidato già congelato.

## Registro

Famiglia `carry_v1`. Ogni configurazione, anche perdente.
**Conteggio: 139 + 4 = 143.**

---

## Esito — 2026-07-18

- **Gate**: 5.51 anni × 46 simboli ✅ (2020-12-25 → 2026-06-30; 1 simbolo
  escluso per regola: storia spot+funding+perp < 5 anni)
- **Primaria (W30/all-positive)**: **+10.32% netto annualizzato** · Sharpe
  1.55 · mesi positivi 80% · IC95 **[+5.63%, +16.48%]** · DSR famiglia
  **100.0%** · concentrazione **3%** (ZEN)
- **Benchmark incondizionato**: +5.79% annuo — il flusso esiste anche senza
  selezione; la selezione lo raddoppia quasi
- **Tutti e 5 i criteri: SUPERATI. Primo candidato promosso in 143 tentativi.**
- DSR cumulativo N=143 (riportato, non vincolante): 85.6%

| Config | ann. netto | Sharpe | mesi+ | IC95 | DSR₄ |
|---|---|---|---|---|---|
| **W30 all-positive** | **+10.32%** | **1.55** | 80% | [+5.6%, +16.5%] | 100% |
| W30 top-10 | +10.62% | 1.36 | 83% | [+5.1%, +17.9%] | 100% |
| W7 all-positive | +9.24% | 1.32 | 73% | [+4.2%, +15.6%] | 99.8% |
| W7 top-10 | +6.84% | 0.80 | 52% | [+0.8%, +14.7%] | 64.5% |

Firma di robustezza opposta a quella del +244 direzionale: **altopiano, non
picco** (3 config su 4 sopra il 9%), concentrazione 3% contro l'87% di
DOGE+SOL, Sharpe 1.55 dentro la banda dei carry reali (0.8-1.5) e non 4x
sopra.

### La seconda verità — il rischio dichiarato si è materializzato nei dati

Spaccato per anno della primaria (descrittivo):

| anno | netto | mesi+ |
|---|---|---|
| 2021 | **+35.49%** | 92% |
| 2022 | +0.38% | 58% |
| 2023 | +6.49% | 100% |
| 2024 | +13.05% | 100% |
| 2025 | +1.89% | 67% |
| 2026 (6 mesi) | **−0.53%** | 50% |

Il pre-registro dichiarava due rischi: *concentrazione temporale nei bull
euforici* e *compressione futura del rendimento*. Sono entrambi nella
tabella: **un terzo del rendimento totale viene dal solo 2021**, e gli ultimi
18 mesi fanno **~+1.4% cumulato** — sotto la soglia operativa del +3% annuo.
Il peggior mese in 5.5 anni è −1.07% (rischio bassissimo, coerente col
delta-neutro), ma il flusso attuale è compresso.

### Lettura onesta

Il carry **funziona come struttura** — il meccanismo è reale, diffuso su ~40
simboli, con drawdown trascurabili — ma **il regime corrente rende ~0-2%
netto**, non il +10% della media storica. Chi entrasse oggi aspettandosi il
numero del backtest ripeterebbe, in forma mite, l'errore del +244: scambiare
la media di una finestra fortunata per l'aspettativa corrente.

**Non aggiungere ora un "filtro di regime"** (es. attivarsi solo quando il
funding trailing è ricco): sarebbe una regola scelta guardando questo
spaccato — la mossa che l'intero impianto vieta. Se la si vuole, è un nuovo
pre-registro.

### Stato

Promosso dai criteri pre-registrati; **holdout NON aperto** e **nessun
go-live**: entrambi restano atti separati e deliberati. La decisione
operativa (se e quanto capitale, con quale aspettativa) spetta all'utente,
con il regime 2025-26 come riferimento prudente.
