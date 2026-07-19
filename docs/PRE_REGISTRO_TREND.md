# Pre-registro — Trend timing sull'azionario (regola di Faber, tasse italiane)

**Data**: 2026-07-19 · **Stato**: APPROVATO (fase 2 dello studio medio termine)
**Famiglia**: `trend_v1` · **Budget**: 3 configurazioni · **Registro alla firma**: 148

---

## L'ipotesi e perché testarla

*La* regola di timing di medio termine pubblicata (Faber 2007): investito
nell'indice quando il prezzo di fine mese è sopra la propria media mobile a
10 mesi, altrimenti in liquidità. La letteratura le riconosce drawdown
dimezzati a rendimento simile — **ma non conta mai le tasse italiane**, e
ogni uscita in utile qui paga il 26%.

**Prior dichiarato**: riduce i drawdown, **perde contro il buy&hold al netto
di tasse e costi**. Il valore del test è chiudere per misura la domanda "il
timing di medio termine può proteggere il mio PAC?" — se il prior regge,
l'IPS ne esce blindato con numeri; se cade, c'è qualcosa da capire.

## Dati e disegno

- S&P 500 mensile 1985-2026 (~499 mesi, ~8 cicli di mercato), indice di
  prezzo in USD; T-bill 3M (FRED DTB3) come rendimento della liquidità.
- **Decisione a fine mese t, posizione per il mese t+1** (nessun lookahead).
- Config: media mobile a **10 mesi (PRIMARIA — la regola pubblicata)**;
  6 e 12 mesi come lettura di robustezza, **non promuovibili**.

## Contabilità dichiarata — con le distorsioni e la loro direzione

- **Tasse**: 26% sul guadagno realizzato a ogni uscita in utile; minusvalenze
  compensabili con plusvalenze successive entro 4 anni. *Nota di direzione*:
  per gli ETF reali la compensazione NON è ammessa (redditi da capitale) —
  la simulazione è quindi **generosa verso il timing**; liquidità remunerata
  al T-bill netto 12.5%.
- **Costi**: 0.1% per lato a ogni switch.
- **Dividendi esclusi** (indice di prezzo): stare fuori dal mercato perde
  anche i dividendi (~2%/anno), non modellati — altra distorsione **a favore
  del timing**. Se il buy&hold vince comunque, vince a fortiori.
- Buy&hold di confronto: stessa serie, tassazione 26% solo alla fine.

## Criterio — dichiarato prima

**Primaria (SMA 10)**: batte il buy&hold se la ricchezza finale netta è
maggiore **e** l'IC bootstrap 95% della differenza di rendimento mensile
esclude lo zero. Altrimenti: prior confermato. Riportati sempre: CAGR netto,
max drawdown, tempo investito, numero di switch, tasse pagate cumulative.

## Registro

Ogni config in `trend_v1`. **Conteggio: 148 + 3 = 151.**

---

## Esito — 2026-07-19

*Compilato a run concluso (sotto).*
