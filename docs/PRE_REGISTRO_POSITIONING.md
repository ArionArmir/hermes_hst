# Pre-registro — Feature di posizionamento (open interest, long/short)

**Data**: 2026-07-17 · **Stato**: DA APPROVARE · **Budget dichiarato**: 4 configurazioni

---

## Perché questo esperimento riapre una dimensione "chiusa"

Il 2026-07-17 la dimensione feature è stata dichiarata *"chiusa per
indisponibilità di dati: OI, long/short e order book hanno 20 giorni di
ritenzione"*. **Era falso.** Avevo verificato solo l'API REST e inferito che
non esistessero altre fonti — la stessa classe di errore già commessa cinque
volte in sessione. I dump pubblici di `data.binance.vision` contengono
(verificato su S3, file alla mano, 2026-07-17):

- **`metrics`**: `sum_open_interest`, OI in valuta, 3 ratio long/short (top
  trader per conti, top trader per posizioni, globale), taker buy/sell volume
  ratio — **dal 2020-09-01, ogni 5 minuti, gratis**. La storia comune dei
  nostri dati parte dal 2020-09-14: copertura completa.
- `bookDepth` (±1-5%) dal 2023-01-01 — fuori scope qui, eventuale follow-up.

Il posizionamento è la prima informazione **ortogonale al prezzo** mai
disponibile sull'intera finestra: le 18 feature attuali sono tutte
trasformazioni della stessa serie OHLCV+flow.

## Il prior di chi scrive, dichiarato prima

**Moderatamente più alto che per il funding, ma l'esito atteso resta
H7-nulla.** A favore: informazione strutturalmente nuova (chi è posizionato,
non solo cosa ha fatto il prezzo). Contro: questi stessi dati sono su CoinGlass
sotto gli occhi di tutti dal 2019 — l'argomento dell'efficienza si applica,
attenuato ma presente. E il baseline su cui si innesta è un modello che su 47
simboli ha Sharpe/trade ≈ 0: le feature nuove dovrebbero *creare* un edge, non
migliorarne uno.

**Nota di scope**: il funding rate resta l'altro asterisco aperto sulla
dimensione feature. NON è incluso qui — mescolare due famiglie di feature in un
braccio renderebbe un eventuale positivo non attribuibile. Se ne occuperà un
eventuale pre-registro separato.

---

## Disegno — confronto appaiato con/senza, come `order_flow_prereg`

**2 configurazioni di trading × 2 bracci (baseline 18 feature / baseline + 4
posizionamento) = 4 run.** Il confronto è appaiato: entrambi i bracci di una
coppia condividono config, fold, universo e costi — l'unica differenza è
l'informazione data al modello.

| # | Config (congelata) | Braccio | Ruolo |
|---|---|---|---|
| 1 | h10 / 0.5% fisso / q=1% / U47 | 18 feature | baseline (già noto da breadth: SR −0.0170) |
| 2 | h10 / 0.5% fisso / q=1% / U47 | **18 + 4 pos.** | **TEST PRIMARIO** |
| 3 | h5 / 0.5% fisso / q=1% / U47 | 18 feature | baseline |
| 4 | h5 / 0.5% fisso / q=1% / U47 | 18 + 4 pos. | coppia di controllo |

Universo: i **47** della regola breadth (pre-2021, attivi, non sigillati) —
usare i 7 sarebbe rientrare nella selezione già smascherata tre volte.
Walk-forward 4 fold, `max_holding = horizon`, uscite 3×ATR, etichetta a
orizzonte fisso, frequenza di segnale q=1% ricalibrata per fold sulla
calibrazione. Tutto identico agli esperimenti precedenti.

## Le 4 feature — definite prima di guardare i dati

Dai `metrics` a 5 minuti, allineati alle candele 1h con **merge_asof
all'indietro** (ultimo snapshot con `create_time ≤` chiusura barra; mai in
avanti: sarebbe lookahead), tolleranza 2 ore, oltre la quale la riga è NaN e
viene **esclusa** (mai riempita):

1. `oi_change_1` = variazione % di `sum_open_interest` sull'ultima barra
2. `oi_ratio_20` = OI / media mobile 20 barre dell'OI
3. `lsr_ratio_20` = `count_long_short_ratio` / propria media mobile 20 barre
4. `taker_lsr_ratio_20` = `sum_taker_long_short_vol_ratio` / propria media
   mobile 20 barre

Stile identico alle feature esistenti (rapporti su medie mobili 20, scale-free).
Nessuna variante verrà provata: queste quattro, così definite, o niente.

## Validazione obbligatoria prima del run

Come per breadth: intersezione (candele ∩ metrics) sui 47 simboli **≥ 4.5
anni**, altrimenti **STOP** e diagnosi esplicita. I metrics possono avere
buchi o partire in ritardo su alcuni simboli: meglio fermarsi che misurare su
una finestra silenziosamente diversa da quella dichiarata.

> **Emendamento del 2026-07-17, autorizzato, prima di qualsiasi risultato.**
> Il gate originale era ≥ 5 anni, derivato dall'affermazione "metrics dal
> 2020-09-01: copertura completa". Quella affermazione era stata verificata su
> **un solo simbolo** (BTCUSDT) e generalizzata ai 47 — la sesta occorrenza
> della stessa classe di errore (assumere invece di verificare). Realtà,
> misurata a download completo: **solo BTCUSDT ha il backfill dal 2020-09**;
> Binance pubblica i dump metrics per tutti gli altri dal **2021-12-01**.
> Finestra reale: **4.62 anni** (2021-12-01 → 2026-07-16).
>
> Il gate è emendato a 4.5 anni perché il valore 5 era strumentale
> all'aspettativa di finestra, non un principio: 4.62 anni × 47 simboli sono
> ~1.9M barre, quasi 5× il dataset storico originale. Il gate ha fermato il
> run **prima che un solo modello venisse addestrato**: nessun risultato è
> stato visto, l'emendamento è legittimo (stessa forma della correzione
> 38→47 nel pre-registro breadth).
>
> **Conseguenza dichiarata**: i fold di test coprono ~2023-09 → 2026-07,
> diversi dalla finestra degli esperimenti precedenti. I confronti *interni*
> alle coppie (baseline vs positioning, stessi fold) restano pienamente
> validi — è il disegno appaiato a proteggerli. I confronti con i numeri
> storici (es. SR −0.0170 di breadth su U47) diventano solo indicativi:
> il baseline viene rimisurato apposta dentro questo esperimento.

---

## Ipotesi

- **H7a (la domanda)** — L'informazione di posizionamento migliora il segnale:
  il braccio positioning batte il baseline in Sharpe/trade in **entrambe** le
  coppie. Con 2 coppie è evidenza indicativa, non conclusiva: la decisione vera
  è H7b. *Falsificata se:* vince in ≤ 1 coppia su 2.
- **H7b (promozione)** — La config #2 supera tutti i criteri sotto.
- **H7-nulla** — Il posizionamento non crea edge. **Esito atteso.** A quel
  punto la dimensione feature resta aperta su un solo asterisco (funding), e
  l'unica famiglia dati inesplorata con storia utile è `bookDepth` (3.5 anni).

## Criterio di successo — dichiarato prima di guardare

1. **DSR > 90%** con **`n_trials = 129`** (125 spesi + 4 nuovi, cumulativo).
2. **Tutti e 4 i fold positivi.**
3. **Nessun simbolo oltre il 60% del profitto lordo.**
4. **Bootstrap a blocchi mensili: IC 95% > 0** — **gate primario** (47 cripto
   correlate: il DSR sovrastima; se dissentono vince il bootstrap).

H7a positiva ma H7b negativa → **non si promuove nulla e non si apre
l'holdout**: si è imparato che l'informazione aiuta, il che motiva il follow-up
su bookDepth — ma "meglio di zero" non è "dimostrato".

## Uso dell'holdout

Invariato: lotti A e B sigillati, apertura solo se la #2 supera tutto, atto
separato su un solo candidato.

## Registro

Famiglia `positioning_v1`. Ogni run, anche perdente. **Conteggio: 125 + 4 =
129.**

---

## Esito — 2026-07-17, ~3.5 minuti di calcolo

- **Validazione intersezione**: 4.62 anni (gate emendato ≥ 4.5: superato).
  40.431 barre comuni, 2021-12-01 → 2026-07-16.
- **Run completati**: 4 / 4
- **H7a — coppie vinte dal positioning: 1 / 2 → FALSIFICATA**
- **H7b — FALSIFICATA** su ogni criterio (tranne la concentrazione)
- **Holdout**: **NON aperto.** Lotti A e B sigillati.

| Config | Braccio | PnL | trade | fold+ | Sharpe/trade | IC95 bootstrap |
|---|---|---|---|---|---|---|
| h10 | baseline 18 | −307.38 | 1390 | 0/4 | −0.0312 | [−1000, +376] |
| h10 | **positioning 22** | −132.18 | 1098 | 1/4 | **−0.0155** | [−653, +438] |
| h5 | baseline 18 | −148.51 | 1727 | 2/4 | −0.0144 | [−706, +383] |
| h5 | positioning 22 | −185.72 | 1446 | 2/4 | −0.0198 | [−692, +279] |

### Lettura

Il positioning "vince" su h10 (−0.0155 contro −0.0312) e perde su h5 (−0.0198
contro −0.0144): **1/2, cioè una monetina**, e tutti e quattro i bracci sono
negativi. L'informazione di posizionamento — OI e ratio long/short, la prima
famiglia ortogonale al prezzo mai testata — **non crea un edge** dove il
prezzo non ne trovava. Il prior dichiarato (H7-nulla) è confermato.

Da notare che il baseline h10 su questa finestra (4.62 anni, 2021-12→2026-07)
dà SR −0.0312 contro il −0.0170 della finestra breadth (5.56 anni): coerente
con "nessun edge, solo rumore attorno a zero" — il numero balla col periodo,
come deve.

### Nota sul conteggio

Il registro riporta **5** tentativi per `positioning_v1` invece dei 4
dichiarati: due run sono crashati a metà (dtype dei timestamp, poi `inf` da OI
a zero) e il baseline h10 completato prima del secondo crash è stato
registrato due volte. Il sovraconteggio è conservativo (alza l'asticella) e si
lascia agli atti. **Totale registro: 131.**

### Cosa resta sulla dimensione feature

- **funding rate** — l'asterisco aperto dall'analisi metodologica: storia
  profonda via API (verificata dal 2020) e dump mensili. Mai testato.
- **bookDepth** — profondità ±1-5% dal 2023-01: ~3.5 anni, finestra più corta
  ma informazione di microstruttura vera (imbalance).

Ciascuno richiede un pre-registro proprio. Nessun altro dato con storia utile
esiste gratis; oltre, si compra (CoinGlass/Tardis) o si registra da oggi.
