# Pre-registro — Momentum cross-sectional crypto (mensile, long-only)

**Data**: 2026-07-19 · **Stato**: APPROVATO (fase 3 dello studio medio termine)
**Famiglia**: `momentum_crypto_v1` · **Budget**: 3 configurazioni · **Registro alla firma**: 151 (dopo trend_v1)

---

## L'ipotesi

A ogni fine mese, classifica i ~47 simboli per rendimento trailing e tieni i
**top-10 equal-weight** per il mese successivo. La domanda: **la selezione
momentum batte il tenere tutti e 47?** Long-only (shortare alt spot è
operativamente una fantasia per un retail).

**Prior dichiarato: H-nulla** — letteratura crypto debole post-2018, e
sappiamo cosa fanno i costi di rotazione. Chiude l'ultima famiglia di
anomalie testabile gratis sui dati in casa.

## Dati e disegno

- Chiusure mensili spot dei 47 (già in casa), USD, dal listing di ciascuno.
- Un simbolo entra nell'universo del mese quando ha ≥ lookback+1 mesi di
  storia; il benchmark del mese è l'equal-weight di TUTTI i simboli
  disponibili quel mese (stesso universo per entrambi i bracci: confronto
  appaiato).
- **Decisione a fine mese t, posizione per t+1** (nessun lookahead).
- Config: lookback **3 mesi (PRIMARIA)**; 1 e 6 mesi robustezza, non
  promuovibili.
- **Costi**: 0.15% per lato spot sul turnover effettivo (misurato nel run);
  il benchmark paga solo l'ingresso dei nuovi listing. Tasse ignorate:
  colpiscono i due bracci in modo simile (confronto appaiato, dichiarato).

## Criterio — dichiarato prima

**Primaria (3m)**: batte il benchmark se la media della differenza mensile
(top10 − tutti) è positiva **e** l'IC bootstrap 95% della differenza esclude
lo zero. Altrimenti H-nulla. Riportati sempre: Sharpe di entrambi, turnover,
costo cumulato della rotazione.

## Registro

Ogni config in `momentum_crypto_v1`. **Conteggio: 151 + 3 = 154.**

---

## Esito — 2026-07-19

*Compilato a run concluso (sotto).*
