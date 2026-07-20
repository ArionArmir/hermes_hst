# Pre-registro — Test in avanti della soglia 0.50 (paper trading)

**Data**: 2026-07-19 · **Stato**: APPROVATO (piano autorizzato dall'utente)
**Famiglia registro**: `forward_v1` · **Capitale**: solo paper, mai reale

---

## Perché un test in avanti, e perché proprio 0.50

I dati futuri sono **vergini per costruzione**: nessuno dei 148 tentativi a
registro li ha toccati. Il forward è l'unico canale di validazione pulito che
non spende l'holdout (lotti A e B restano sigillati).

La soglia 0.50 è il candidato giusto per questo canale, per due ragioni
indipendenti dal suo backtest:
1. **Predetta a priori** dall'analisi dei bucket (2026-07-16): la fascia
   0.45-0.55 risultava profittevole *prima* di qualunque sweep.
2. **Confine strutturale**: sopra 0.50 i segnali up/down sono mutuamente
   esclusivi per costruzione. Non è un valore tarato.

Il suo backtest (+244.65, 4/4 fold) resta NON promuovibile (DSR 21.4% col
conteggio onesto): questo esperimento esiste proprio perché il backtest non
basta. Prior dichiarato: **Sharpe vero ≈ 0** — l'esito atteso è un nulla di
fatto; un esito positivo varrebbe perché contro il prior.

## Il cambio — uno solo

```
config/trading_params.yaml: ml_confidence_threshold: 0.55 -> 0.50
```

**Nient'altro.** Filtri, uscite, taglie, simboli, modello: invariati.
Qualunque ulteriore modifica alla config a esperimento in corso **termina
l'esperimento** (nuovo pre-registro per ripartire). Un esperimento = una
variabile.

## Lettura dell'esito — dichiarata prima

- **Quando**: al raggiungimento di **100 trade chiusi** oppure il
  **2027-01-19**, quello che arriva prima. Nessuna lettura intermedia ha
  potere decisionale.
- **Cosa**: Sharpe/trade, PnL netto paper, IC bootstrap sui trade; confronto
  con l'aspettativa dichiarata (≈0) e con il backtest (+0.065/trade).
- **Perché niente letture intermedie**: a ~3.4 trade/settimana (a macchina
  accesa), una settimana verde ha ~50% di probabilità anche a edge zero. I
  colori settimanali sono rumore per costruzione.
- **Aspettativa sul singolo test notturno, fissata ora**: P(≥1 trade in una
  notte) ≈ 20%. **L'esito più probabile di stanotte è zero trade**, e sarà
  coerenza, non fallimento: la verifica notturna è FUNZIONALE (la macchina
  valuta, filtra, registra?), non di edge.
- **Tentazione già prezzata**: se domattina è zero, la mossa "scendiamo a
  0.45" è vietata da questo documento — a 0.45 la misura dice −223.81 in 4.7
  anni (i segnali up/down smettono di essere esclusivi). Non è un esperimento,
  è spread regalato.

## Clausola capitale reale

Questo esperimento **non può portare a denaro reale in nessun caso** senza un
nuovo pre-registro con criteri propri — e resta comunque fuori dall'IPS
(docs/PRE_REGISTRO_INVESTIMENTO.md), che non viene modificato da nulla di ciò
che accade qui.

## Checklist funzionale del test notturno (domattina)

- [ ] telemetria `ml_conf_*`: valutazioni notturne presenti, `max_oggi` per simbolo
- [ ] tabella `signals`: righe presenti se qualche segnale ha superato 0.50
      (inclusi gli scarti dei filtri con l'esito)
- [ ] eventuali ordini paper nello store posizioni
- [ ] watchdog: nessun allarme

## Registro incidenti

**2026-07-20 — Deriva di configurazione (rientrata, impatto provato nullo).**
Col riavvio della macchina del 2026-07-19 sera, Redis è ripartito da uno
snapshot precedente al lancio del test: `ml_confidence_threshold` è tornato
silenziosamente a 0.55 (il valore pre-esperimento) e il motore l'ha caricato
al riavvio delle 20:57 — Redis vince sul YAML, che dichiarava 0.50.
Scoperta il 2026-07-20 dall'operatore umano guardando la pagina
Configurazione; nessun controllo automatico l'aveva vista (da qui il check
"config drift" nel watchdog).

Analisi di impatto: replay dell'intera finestra sospetta (2026-07-16 00:25 →
2026-07-20 17:29 UTC) con stesse candele, stesse feature e stesso champion
dell'inference, pipeline validata al quarto decimale contro la telemetria
live `ml_conf_*`. **Confidenza massima vista: 0.4392.** Nessuna ora ha
raggiunto 0.50 su nessuno dei 7 simboli: a soglia 0.50 o 0.55 il
comportamento del sistema è stato identico per costruzione (zero segnali,
zero trade in entrambi i mondi). Soglia 0.50 ripristinata e ricaricata da
motore e inference il 2026-07-20 17:29.

Decisione (umana, 2026-07-20): l'esperimento **continua** — la clausola
punisce i cambi che possono alterare gli esiti, e questa deviazione è
dimostrabilmente priva di effetti. L'incidente resta agli atti; la lettura
finale dovrà citarlo.

## Esito

*Da compilare SOLO alla data/soglia di lettura dichiarata.*

- [ ] Trade accumulati: __ · periodo effettivo di uptime: __
- [ ] Sharpe/trade: __ · IC bootstrap: __
- [ ] Verdetto vs prior: __
