# Pre-registro — Ampiezza dell'universo (breadth)

**Data**: 2026-07-17 · **Stato**: DA APPROVARE · **Budget dichiarato**: 5 configurazioni

---

## L'ipotesi, in una riga

**Non ci manca un segnale migliore: ci mancano occasioni per usarlo.**

Dopo 115 tentativi la migliore configurazione mai osservata è `h10 / q=1% /
orizzonte fisso`: **Sharpe/trade 0.1190**, quasi il doppio dei 0.0651 della
produzione. Non è promuovibile per un solo motivo: **510 trade non bastano a
dimostrarla** (DSR 34.7%).

Servono **1.344 trade** a quello Sharpe per superare l'asticella. Sono 2.6×.
Con 7 simboli ne facciamo 510. Ne servirebbero **~18**.

## Perché questa leva e non le altre

I 115 tentativi spesi hanno una cosa in comune: cercavano tutti di alzare la
**qualità del segnale** (order flow, soglie, orizzonti, etichette). La Legge
Fondamentale della Gestione Attiva dice:

> **IR ≈ IC × √breadth**

`IC` è quanto sei bravo a predire. `breadth` è quante scommesse indipendenti
fai. **Abbiamo lavorato solo su IC.** La breadth è ferma a sette simboli — quelli
scelti il primo giorno, mai messi in discussione, esattamente come il target.

Le altre due strade sono più strette di quanto sembrasse:

- **Feature**: quasi chiusa per indisponibilità di dati. Open interest,
  long/short ratio e order book hanno **20 giorni** di ritenzione su Binance
  (verificato 2026-07-17). Non si backtesta ciò di cui non esiste storia. Resta
  il solo funding rate: una feature, a 8 ore. L'order flow — quattro feature,
  informazione genuinamente nuova — non ha spostato nulla.
- **Timeframe**: peggiora l'economia più di quanto migliori la statistica. Il
  movimento tipico su 5 barre copre il costo **1.76×** a 5m contro **9.27×** a
  1h (misurato). Più dati (11× a 5m) ma edge per trade che collassa. 1h sembra
  già vicino all'ottimo.

**Più simboli aggiungono dati, non tentativi**: alzano `n` senza alzare `N`. È
l'unica leva che muove il rapporto a nostro favore senza pretendere un segnale
migliore.

---

## L'universo — regola meccanica, dichiarata prima

> Tutti i perpetual USDT su Binance Futures **quotati prima del 2021-01-01** e
> **attualmente attivi**, esclusi i simboli dell'holdout sigillato.

Nessun criterio che dipenda dai rendimenti. Con posizioni da 150 USDT la
liquidità non è vincolante per nessun perpetual quotato, quindi **nessun filtro
di volume**: aggiungerne uno sarebbe una manopola in più da tarare.

Stato al 2026-07-17 (letto da `data/historical/`, dal manifesto dell'holdout e
da `exchangeInfo`, **non da liste scritte a mano** — scrivendola a mano avevo
dimenticato UNIUSDT, che risultava "disponibile" pur essendo già bruciato):

| | N |
|---|---|
| Perpetual USDT attivi quotati prima del 2021-01-01 | 58 |
| Sigillati (holdout, vietati) | 11 |
| **Universo = 58 − 11** | **47** |

di cui 16 già in `data/historical/` (i nostri 7 + 9 toccati dallo screening del
2026-07-16) e 31 mai scaricati. **Universo: 47 simboli.** Ne servono ~18: il
margine è ampio.

> **Correzione del 2026-07-17, prima di qualsiasi risultato.** La prima
> stesura diceva "7 operativi + 31 nuovi = 38", dimenticando i **9 simboli già
> toccati ma pienamente eleggibili** (ADA, ATOM, AVAX, DOT, FIL, LINK, LTC,
> NEAR, UNI): sono pre-2021, attivi, non sigillati. Errore aritmetico mio,
> della stessa specie della lista scritta a mano che ometteva UNIUSDT.
>
> Vale la **regola** (47), non il mio conteggio (38): la regola è l'impegno
> sostanziale. Includere i 9 non introduce distorsione — essere "bruciati"
> impedisce di **validare**, non di fare ricerca, e tutto `data/historical/` è
> dato di ricerca per definizione. Qui non si sceglie fra loro: si prendono
> tutti per regola.
>
> La correzione è fatta **prima di aver visto un solo risultato**: è l'unico
> momento in cui emendare un pre-registro è legittimo.

### Perché la regola risolve anche un problema tecnico

`_align_common_index` fa l'**intersezione** degli indici: un solo simbolo
delistato troncherebbe l'intera finestra alla sua data di uscita, e un simbolo
giovane la accorcerebbe. Richiedendo "quotato prima del 2021 e ancora attivo",
le storie sono già allineate e l'intersezione perde ~3 mesi (da 2020-09 a
~2020-12). **Non serve modificare `backtest_joint`.**

*Validazione obbligatoria prima del run*: verificare che l'intersezione dei 47
sia ≥ 5 anni. Se è più corta, il run **si ferma** e il problema si affronta
esplicitamente — non si procede su una finestra silenziosamente accorciata.

> **Il gate è scattato al primo tentativo, ed è servito.** L'intersezione dei
> 47 risultava **0.99 anni**: 9 simboli (ADA, ATOM, AVAX, DOT, FIL, LINK, LTC,
> NEAR, UNI) avevano esattamente 8.760 barre — un anno tondo. Erano stati
> scaricati durante lo screening del 2026-07-16, quando `HISTORY_DAYS` era
> ancora 365, e lo script di download li aveva saltati perché controllava se il
> file *esistesse*, non se fosse *completo*.
>
> Senza il gate avremmo misurato la breadth su **un anno** invece di 5.5 —
> tornando al regime da 15-40 trade per fold che questa intera linea di lavoro
> esiste per superare, e senza accorgercene. Corretto: il download ora
> riscarica ogni parquet con meno di 5 anni, dato che la regola dell'universo
> garantisce quotazione pre-2021.

### Survivorship bias — dichiarato, non nascosto

I 58 simboli pre-2021 sono **sopravvissuti**: i delistati non compaiono fra gli
attivi. L'universo è quindi ottimisticamente distorto.

Conseguenza asimmetrica, ed è il motivo per cui accettiamo il bias:
**un esito negativo resta conclusivo** (se fallisce con il vento a favore,
fallisce), **un esito positivo resta sospetto** e andrebbe confermato
sull'holdout — che per questo è meno distorto: il lotto B contiene EOSUSDT,
delistato nel 2025, tenuto apposta.

---

## Spazio di ricerca — 5 configurazioni

La configurazione di trading è **congelata**: `h10 / soglia target 0.5% fissa /
orizzonte fisso / q=1%`, `max_holding=10`, uscite 3×ATR, walk-forward a 4 fold.
**Non c'è niente da tarare**: varia solo l'ampiezza dell'universo.

| Universo | Simboli |
|---|---|
| U7 | 7 estratti dai 47 |
| U17 | 17 estratti dai 47 |
| U27 | 27 |
| U37 | 37 |
| **U47** | tutti e 47 — **test primario** |

I sottoinsiemi sono **annidati e casuali con seme fissato (42)**: si mescolano i
47 una volta sola e si prendono i primi `n`. Ordinarli per anzianità
correlerebbe con la capitalizzazione (i più vecchi sono i più grandi e meno
volatili) e confonderebbe la curva di scala; sceglierli per rendimento sarebbe
barare.

**La decisione di promozione si prende solo su U47**, dichiarato ora. Le altre
quattro servono a leggere la curva di scala e **non sono candidati**: se U27
risultasse il migliore, non lo promuoveremmo — sceglierlo dopo averlo visto
sarebbe la selezione da cui l'intero impianto ci protegge.

---

## Ipotesi

- **H5a — L'edge generalizza.** Su U47 la configurazione congelata mantiene
  Sharpe/trade > 0 e produce ≥ 1.344 trade. *Falsificata se:* lo Sharpe/trade
  collassa verso zero o diventa negativo aggiungendo simboli — cioè l'edge era
  specifico dei 7 (o, più precisamente, di SOL, che nella config migliore vale
  il 46% del profitto lordo).
- **H5b — La scala segue la Legge Fondamentale.** Lo Sharpe totale cresce
  ~√breadth lungo U7→U47. *Falsificata se:* piatta o decrescente. Una scala
  sublineare è comunque attesa e non falsifica: le cripto sono correlate, e 47
  simboli non sono 47 scommesse indipendenti.
- **H5c — Promuovibile.** U47 supera tutti i criteri sotto.
- **H5-nulla** — L'edge non generalizza. Con la breadth chiusa e target,
  feature e timeframe già chiusi, **le ipotesi accessibili sono esaurite**:
  resterebbe da accettare che questi dati non contengono un edge dimostrabile a
  1h con queste feature. **Esito valido, e va riportato come tale.**

---

## Criterio di successo — dichiarato prima di guardare

1. **DSR > 90%** con **`n_trials = 120`** (115 spesi + 5 nuovi).
2. **Tutti e 4 i fold positivi.**
3. **Nessun simbolo oltre il 60% del profitto lordo.**
4. **Bootstrap a blocchi mensili: IC 95% che esclude lo zero.** Non è un
   accessorio: è il gate che conta di più qui. Il DSR assume trade indipendenti,
   e con 47 cripto correlate non lo sono — **il DSR sovrastima**. Il bootstrap
   mensile ricampiona i mesi e assorbe la correlazione fra simboli dentro il
   mese. **Se DSR e bootstrap dissentono, vince il bootstrap.**

Se U47 fallisce: **H5-nulla**, holdout non aperto, e la ricerca sui dati
storici è chiusa.

---

## Uso dell'holdout

Invariato. Lotti A e B sigillati. Apertura solo se U47 supera tutto, come atto
separato e deliberato, su un solo candidato.

Nota: se U47 passasse, il test sull'holdout avrebbe un valore particolare —
l'holdout è **meno distorto** dell'universo di ricerca (contiene EOSUSDT
delistato) e sono simboli mai guardati. Sarebbe il controllo esatto per il
survivorship bias dichiarato sopra.

---

## Registro

Famiglia `breadth_v1` in `docs/experiment_registry.jsonl`. Ogni tentativo.

**Conteggio**: 115 spesi + 5 = **120**.

---

## Esito

*Da compilare a run concluso.*

- [ ] Configurazioni girate: __ / 5
- [ ] Intersezione storica verificata: __ anni (attesi ~5.5)
- [ ] U47 — trade prodotti: __ / 1.344 richiesti
- [ ] H5a __ · H5b (curva di scala) __ · H5c __
- [ ] Holdout aperto: no / lotto A
