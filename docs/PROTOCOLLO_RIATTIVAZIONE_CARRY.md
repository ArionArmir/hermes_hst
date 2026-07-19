# Protocollo di riattivazione del carry

**Data**: 2026-07-19 · **Stato**: ATTIVO · Scritto col regime BASSO (31°
percentile), apposta: le regole scritte quando non c'è fretta non sono sospette.

---

## La catena, per intero

```
SEMAFORO (descrive, mensile)          ← attivo dal 2026-07-19
   ↓
PAPER EXECUTOR (misura, continuo)     ← attivo dal 2026-07-19, sempre acceso
   ↓  tripwire: fascia RICCA (funding mediano 30gg ≥ 8% lordo annuo,
   ↓            la banda in cui il carry pagava) per DUE rapporti mensili consecutivi
PRE-REGISTRO DI ATTIVAZIONE           ← da scrivere solo allo scatto
   ↓  criteri propri + conferma dal paper (divergenza backtest/live misurata)
AUTORIZZAZIONE DELL'UTENTE            ← sempre umana, mai automatica
   ↓  compatibilità IPS: satellite ≤10%, emendamento se serve
CAPITALE REALE
```

Le soglie discendono da quantità **già dichiarate e misurate**: 8% = mediana
storica del funding (carry_v1), 3% netto = soglia operativa pre-registrata.
Nessun numero di questo protocollo è stato scelto guardando lo spaccato
annuale per ottimizzarlo.

## Il paper executor — cosa fa e cosa non fa

- Esegue la **primaria promossa di carry_v1** (W30, all-positive,
  ribilanciamento il lunedì 00 UTC) su posizioni **simulate** da 100 USDT
  di notional per simbolo, con la **stessa contabilità del backtest**
  (`src/research/carry.py`): funding reale accreditato dall'API, basis
  misurato all'esecuzione, costi 0.28%/ciclo.
- Il suo scopo è **misurare la divergenza tra backtest e realtà** (famiglia
  `carry_paper_v1` nel registro) e tenere la macchina rodata per il giorno
  del tripwire.
- **Non tocca denaro, non può toccarne**: non ha chiavi API di trading.
- Gira sempre, anche a regime compresso: un paper che rende ~1-2% in regime
  magro *conferma* la stima del semaforo — è un dato, non uno spreco.

## Cosa lo scatto del tripwire NON fa

Non apre posizioni reali, non cambia config, non "attiva" nulla da solo.
Fa una cosa sola: il rapporto dell'analista lo scrive a caratteri grandi, e a
quel punto il passo successivo è **scrivere il pre-registro di attivazione**
— con l'evidenza del paper allegata. Da lì in poi, ogni passo è umano.

## Lettura del paper — dichiarata prima

Valutazione della divergenza backtest/live: a **26 settimane** di uptime
cumulativo o **50 eventi di ribilanciamento**, quello che arriva prima.
Metrica: funding incassato paper vs funding atteso dai dati storici dello
stesso periodo; costi e basis realizzati vs assunti. Nessuna lettura
intermedia con potere decisionale.
