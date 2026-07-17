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

## Esito

*Da compilare a run concluso.*

- [ ] Validazione intersezione: __ anni (richiesti ≥ 5)
- [ ] Run completati: __ / 4
- [ ] H7a — coppie vinte dal positioning: __ / 2
- [ ] H7b — primario (h10+pos): DSR __ · fold+ __ · IC95 __
- [ ] Holdout aperto: no / lotto A
