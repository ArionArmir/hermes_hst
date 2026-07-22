# Pre-registro — La firma delle liquidazioni nel nastro dei trade

**Data**: 2026-07-20 · **Stato**: DA APPROVARE · **Budget dichiarato**: 1 run di aggancio + 5 varianti di firma

---

## Il gioco

Dal 2021 Binance censura il feed delle liquidazioni (max 1 evento per simbolo
al secondo), ma ogni liquidazione viene comunque **eseguita sul book come
trade normale** e finisce nel nastro pubblico (aggTrades), senza etichetta.
Dal 2026-07-20 il nostro registratore fornisce etichette vere (campionate)
con timestamp al millisecondo.

La domanda è una sola: **le esecuzioni forzate lasciano nel nastro una firma
riconoscibile senza etichette?** Se sì, con quale errore misurato — perché
un sì aprirebbe la stima di un dataset storico di liquidazioni su anni di
nastro gratuito. Se no, è un risultato che chiude la strada e va registrato
come tutti gli altri.

## Cosa NON è

Non è ricerca di edge: nessun segnale di trading, nessun capitale, nessuna
promessa che la firma — se esiste — sia monetizzabile. Qualunque uso di
trading richiederebbe un pre-registro separato. I 12 simboli dell'holdout
(lotti A e B) sono **esclusi dall'universo** per prudenza, anche se lo studio
non è direzionale: il sigillo non si tocca nemmeno di striscio.

## Dati — verificati prima di scrivere (2026-07-20)

- **Etichette**: `data/liquidations/*.parquet` (recorder live, endpoint
  `/market` corretto oggi). Ritmo osservato: ~1.500 eventi/ora su ~450
  simboli. Campionate per costruzione: max 1/simbolo/secondo.
- **Nastro**: dump giornalieri aggTrades USDT-M di Binance Vision, tick per
  tick, lato aggressore incluso (campo `m`). Verificato: disponibili con
  ≤1 giorno di ritardo, gratuiti.

## La proprietà che rende lo studio ben posto

Per documentazione Binance, lo stream pubblica lo snapshot dell'**ultimo**
ordine forzato per simbolo nella finestra di 1000 ms — *se* almeno una
liquidazione è avvenuta. Quindi, a registratore connesso:

- simbolo-secondo **senza** etichetta → nessuna liquidazione: **negativo pulito**;
- simbolo-secondo **con** etichetta → almeno una liquidazione (quante, non si sa): **positivo certo**.

Precision e recall sono perciò ben definiti **a granularità simbolo-secondo**,
non a granularità evento. Tutte le metriche dello studio vivono lì. I minuti
in cui il registratore era disconnesso (gap noti, macchina spenta) sono
esclusi da entrambe le classi.

## Il prior di chi scrive, dichiarato prima

Recall alto sulle liquidazioni grandi, degradante sulle piccole (una micro
liquidazione da 200$ è indistinguibile dal rumore). Il rischio vero è la
**precision**: uno stop-market volontario aggressivo produce nel nastro la
stessa impronta meccanica di una liquidazione. Se la precision resta bassa
in ogni variante, la risposta onesta è "il nastro da solo non basta" — e
sarebbe coerente col motivo per cui i vendor vendono il dato etichettato
invece di stimarlo.

## Fase 0 — Aggancio (gate, 1 run)

Per ogni etichetta: cercare nel nastro i trade corrispondenti — stesso
simbolo, finestra ±1 s, lato aggressore = lato forzato, prezzo entro lo 0.1%
del prezzo medio dell'ordine, quantità cumulata compatibile (±20%).

**Gate: ≥90% delle etichette agganciate, o STOP** (vorrebbe dire che nastro
ed etichette non parlano la stessa lingua e ogni firma sarebbe costruita
sulla sabbia).

## Fase 1 — Le 5 varianti di firma (dichiarate qui, non dopo)

Candidato = simbolo-secondo del nastro. Feature di base: volume aggressivo
unilaterale nel secondo (percentile per simbolo, calcolato sul giorno
precedente — mai sul giorno giudicato), impatto prezzo nel secondo, numero
di trade nella raffica.

| # | Firma |
|---|-------|
| 1 | volume aggressivo unilaterale > P99 del simbolo |
| 2 | variante 1 + impatto prezzo nel secondo > P95 |
| 3 | variante 1 + ≥3 trade stesso lato nello stesso secondo |
| 4 | variante 2 + sequenza prezzi monotona nel secondo |
| 5 | come 2, ma soglie a P99.5/P99 (versione severa) |

Nessuna ottimizzazione oltre queste 5: le soglie sono scritte qui e non si
ritoccano dopo aver visto i risultati.

**Split temporale**: prima metà del periodo di raccolta = calibrazione dei
percentili; seconda metà = test. Le metriche che contano sono solo quelle
del test.

## Criterio di successo (primario, dichiarato)

Almeno una variante con **precision ≥ 80% E recall ≥ 50%** sul test, a
granularità simbolo-secondo, calcolate sull'intero universo (no cherry
picking di simboli). Qualunque esito intermedio ("funziona solo su BTC")
è descrittivo, non promuovibile.

## Lettura

Alla **prima** di queste condizioni: **≥14 giorni pieni di etichette E
≥50.000 etichette** (attesi in ~2 giorni al ritmo odierno, i 14 giorni
servono a non giudicare su un solo regime di volatilità), oppure il
**2026-08-20** con quello che c'è, purché ≥7 giorni. Prima della lettura:
nessuno sguardo alle metriche del test.

## Registrazione

Fase 0 e ogni variante di Fase 1 = una riga nel registro
(`record_trial("firma_liquidazioni", ...)`): 6 tentativi a budget, inclusi
i perdenti.
