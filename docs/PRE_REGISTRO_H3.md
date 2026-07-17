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

## Esito

*Da compilare a run concluso.*

- [ ] Configurazioni girate: __ / 24
- [ ] H3a — coppie vinte dal triple barrier: __ / 12
- [ ] H3b — promuovibili: __
- [ ] Holdout aperto: no / lotto A
