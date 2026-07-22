# Pre-registro H3 — Triple barrier a frequenza di segnale appaiata

**Data**: 2026-07-17 · **Stato**: DA APPROVARE · **Budget dichiarato**: 24 configurazioni

---

## Perché esiste un secondo pre-registro

Il primo (`PRE_REGISTRO_TARGET.md`) ha lasciato **H3 senza risposta per un
errore di disegno**, non per mancanza di segnale. Fissava la soglia di
probabilità a 0.50 per tutte le configurazioni, con una giustificazione
strutturale valida (sopra 0.50 i segnali up/down sono mutuamente esclusivi) ma
incompleta: **cambiare l'etichetta cambia le probabilità a priori delle classi,
e quindi cosa quella soglia significa.**

Con barriere strette quasi ogni barra ne tocca una: la classe FLAT crolla dal
47.9% all'11.8%, le priorità di UP/DOWN salgono da ~26% a ~44%, e a parità di
soglia il triple barrier trada molto di più:

| orizzonte | fissa: PnL / trade | triple barrier: PnL / trade | rapporto trade |
|---|---|---|---|
| h2 | +38.91 / 832 | −130.33 / 656 | **0.8×** |
| h5 | +244.65 / 880 | −439.71 / 1880 | 2.1× |
| h10 | +245.77 / 778 | −618.36 / 3416 | 4.4× |
| h20 | +401.32 / 967 | −613.02 / 3987 | 4.1× |

Non confrontavo due etichette: confrontavo un filtro stretto contro uno largo.

**Ma il confondimento non spiega tutto.** A h2 il triple barrier trada *meno* e
perde comunque. Questo è un indizio contrario a H3 che il nuovo disegno deve
poter cogliere: se TB perde anche a frequenza appaiata, l'ipotesi è morta e va
dichiarata tale.

Questa è una **ricerca nuova**, con budget che si somma ai 91 tentativi già
spesi. Non è un "aggiustamento" della precedente: allargare uno spazio dopo
aver visto i risultati è il meccanismo che i pre-registri esistono per
impedire.

---

## La correzione: frequenza di segnale appaiata

Invece di fissare la soglia di probabilità, si fissa la **frazione di barre che
generano un segnale** (`q`). Per ogni fold, dopo l'addestramento, la soglia è
il quantile `1−q` delle probabilità predette **sul set di calibrazione**, e poi
viene applicata al test.

Il set di calibrazione fa parte del train: **nessun lookahead**. Scegliere la
soglia sul test sarebbe un leak che gonfierebbe tutto.

Così le due etichette vengono confrontate a parità di occasioni prese, e la
domanda diventa quella giusta: *"a parità di quanti trade fai, quale etichetta
sceglie i trade migliori?"*

La normalizzazione **non è un parametro cercato**: è una regola deterministica
per rendere il confronto alla pari. Non consuma tentativi oltre ai 24
dichiarati.

---

## Spazio di ricerca — 24 configurazioni

| Dimensione | Valori | N |
|---|---|---|
| Orizzonte (barre) | 2, 5, 10, 20 | 4 |
| Etichetta | orizzonte fisso, triple barrier | 2 |
| Frequenza di segnale `q` | 1%, 2%, 4% | 3 |

**4 × 2 × 3 = 24.** Nessuna aggiunta a run iniziato.

`q = 2%` è il regime in cui opera la configurazione di produzione (misurato:
1.97% delle barre di calibrazione a soglia 0.50); 1% e 4% coprono un filtro
più stretto e uno più largo.

### Cosa resta costante

- **Soglia del target: 0.5% fissa** per entrambe le etichette (per il triple
  barrier definisce le barriere). Scelta perché è la migliore nota nel braccio
  già testato: usare il controllo più forte rende H3 **più difficile** da
  dimostrare, non più facile. H1 (soglia su ATR) è già falsificata e non
  ritorna.
- **`max_holding` = orizzonte**, come nel primo pre-registro.
- Feature (18 con order flow), modello (XGBoost + Platt), walk-forward a 4 fold
  con gli stessi confini, i 7 simboli operativi, costi, uscite 3×ATR.

---

## Ipotesi

- **H3a (la domanda vera)** — A frequenza di segnale appaiata, il triple
  barrier produce uno Sharpe/trade superiore all'orizzonte fisso. Confronto
  **appaiato** su 12 coppie (4 orizzonti × 3 frequenze). *Falsificata se:* il
  triple barrier vince in ≤ 6 coppie su 12 (test dei segni, p > 0.05 a 9/12).
- **H3b** — Esiste una configurazione promuovibile. *Falsificata se:* nessuna
  supera i criteri sotto.
- **H3-nulla** — Il triple barrier non è migliore, né a frequenza appaiata.
  L'indizio di h2 (perde tradando meno) diventa la spiegazione. **Esito valido
  e da riportare.**

H3a è separata da H3b di proposito: si può rispondere alla domanda
scientifica ("l'etichetta consapevole del percorso è migliore?") anche se
nessuna configurazione è promuovibile in assoluto. Sono due cose diverse e
oggi le stavo confondendo.

---

## Criterio di successo — dichiarato prima di guardare

**Per H3a** (confronto appaiato, non richiede promuovibilità): triple barrier
vince in **≥ 9 coppie su 12**. Sotto il test dei segni bilaterale, 9/12 dà
p = 0.146 e 10/12 p = 0.039 — dichiaro **9/12 come soglia indicativa** e
**10/12 come significativa**. Sotto 9/12: H3 falsificata, chiusa.

**Per H3b** (promozione), tutti insieme:

1. **DSR > 90%** con **`n_trials = 115`** — il conteggio **cumulativo** di tutto
   ciò che è stato provato su questi dati (91 precedenti + 24 nuovi), non i 24
   della sola famiglia. È la scelta conservativa: ciò che provo oggi è stato
   scelto guardando i 91 di prima, quindi quelli fanno parte della selezione.
2. **Tutti e 4 i fold positivi.**
3. **Nessun simbolo oltre il 60% del profitto lordo** (metrica corretta dopo il
   bug del 3420%).
4. Gate di robustezza: 6 fold, bootstrap mensile.

Se H3b fallisce ma H3a passa: **non si promuove nulla e non si apre l'holdout.**
Si è imparato che l'etichetta è migliore, il che indirizza la ricerca
successiva — ma "migliore di una cosa che non funziona" non è promuovibile.

---

## Uso dell'holdout

Invariato. Lotti A e B **sigillati**. Apertura solo se una configurazione supera
tutto, come atto separato e deliberato, su **un solo** candidato.

---

## Registro

Famiglia `target_h3_matched_v1` in `docs/experiment_registry.jsonl`. Ogni
tentativo, anche perdente.

**Conteggio**: 91 spesi + 24 = **115**.

---

## Esito — 2026-07-17, 3.8 minuti di calcolo

- **Configurazioni girate**: 24 / 24
- **H3a — coppie vinte dal triple barrier: 0 / 12**
- **H3b — promuovibili: 0** (DSR massimo 34.7%, serve > 90%)
- **Holdout**: **NON aperto**. Lotti A e B sigillati.

Risultati in `docs/h3_matched_results.csv`, tentativi in
`docs/experiment_registry.jsonl` sotto `target_h3_matched_v1`.

### H3a — FALSIFICATA, e non di misura

L'orizzonte fisso vince **tutte e 12 le coppie**. Test dei segni bilaterale:
**p = 0.00049**. Non è "il triple barrier non è migliore": è
**significativamente peggiore**, con Sharpe/trade negativo in tutte e 12 le
configurazioni (da −0.06 a −0.14) contro un orizzonte fisso fra −0.02 e +0.12.

| | q=1% | q=2% | q=4% |
|---|---|---|---|
| **h2** | fisso +0.0121 · TB −0.1011 | fisso +0.0055 · TB −0.0997 | fisso −0.0231 · TB −0.1309 |
| **h5** | fisso +0.0436 · TB −0.1366 | fisso +0.0145 · TB −0.1199 | fisso −0.0129 · TB −0.0982 |
| **h10** | fisso **+0.1190** · TB −0.0719 | fisso +0.0107 · TB −0.0615 | fisso −0.0125 · TB −0.0835 |
| **h20** | fisso +0.1113 · TB −0.0847 | fisso +0.0535 · TB −0.0733 | fisso +0.0041 · TB −0.0640 |

### La normalizzazione ha funzionato solo a metà — e non cambia la conclusione

La frequenza di segnale è appaiata **sulla calibrazione**, ma sul test il
triple barrier trada ancora 1.3-3.2× di più. Le soglie calibrate erano quasi
identiche (es. h5 q=1%: 0.507 per entrambe), quindi la differenza nasce dopo:
il TB cambia direzione più spesso, e ogni inversione è un trade in più.

**Controllo decisivo**: se fosse il numero di trade a spiegare tutto, una
configurazione a orizzonte fisso con *più* trade dovrebbe fare peggio di un
triple barrier con *meno*. Non succede mai:

> TB h5 q=1% — **1416 trade**, SR **−0.1366**
> La *peggiore* fissa con più trade (2389) — SR **−0.0231**

L'orizzonte fisso trada 1.7× di più e fa comunque 6× meglio. **Il conteggio dei
trade non è la causa.** H3 è chiusa.

### Perché (ipotesi, non dimostrata)

Con barriere a ±0.5% e orizzonti di 5-20 ore, *quale* barriera venga toccata
per prima è dominato dal rumore di breve periodo, non dalla direzione: è quasi
una monetina. L'etichetta a orizzonte fisso conserva invece la piccola
componente prevedibile del rendimento. Il triple barrier descrive meglio ciò
che accade a un trade, ma **descrive una cosa meno prevedibile**. Coerenza con
la regola di trading e prevedibilità sono in conflitto, e abbiamo scoperto da
che parte pende.

### Il risultato collaterale più interessante

**h10, q=1%, orizzonte fisso**: PnL +337.17, 510 trade, 3/4 fold,
**Sharpe/trade 0.1190 — il più alto mai osservato**, quasi il doppio dei 0.0651
della produzione. DSR comunque solo **34.7%**, perché 510 trade non bastano a
dimostrarlo.

Si vede un andamento netto: **più il filtro è selettivo (q basso), più alto è
lo Sharpe per trade** — coerente con l'analisi dei bucket del 2026-07-16 (il
modello è monotòno: più confidenza, più rendimento). Ma meno trade significano
meno evidenza, e l'asticella del DSR sale al calare di `n`. È lo stesso muro di
sempre, da un'altra angolazione.

### Conteggio tentativi

91 + 24 = **115**.

### Cosa resta

Il target è chiuso come leva: né la soglia, né l'orizzonte, né l'etichetta lo
sbloccano. Le ipotesi rimaste non sono più sul target ma sulle **feature** (le
18 non contengono l'informazione) o sul **timeframe orario** (l'edge, se c'è,
non vive a 1h). Entrambe richiedono un pre-registro nuovo.
