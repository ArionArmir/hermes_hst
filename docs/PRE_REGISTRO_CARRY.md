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

## Esito

*Da compilare a run concluso.*

- [ ] Gate: __ anni × __ simboli
- [ ] Primaria — ann. netto: __ · IC95: __ · mesi+: __ · conc.: __
- [ ] Benchmark incondizionato (descrittivo): __
- [ ] Promuovibile: sì/no
