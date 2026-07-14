# Training del modello ML

Come funziona l'addestramento del modello XGBoost (`config/models/champion.pkl`)
usato da `src/inference/main.py` per generare i segnali di trading.

## Punto d'ingresso: `train_all_models.py`

```bash
python train_all_models.py
```

È l'unico script da lanciare per riaddestrare il modello. Legge i simboli da
`config/trading_params.yaml` — aggiungere/rimuovere un simbolo dalla config e
rilanciare questo script è tutto ciò che serve, nessuna altra modifica.

Non confonderlo con `optimize_models.py` (repo root): quello ottimizza i
parametri **rule-based** di `ATRExitModel`/`VolumePatternAnalyzer` via
yfinance, è completamente indipendente dal modello XGBoost e oggi gira solo
su un simbolo hardcoded (`BTC-USD`) — non è coinvolto nel training ML.

## Approccio: modello unico pooled (Approccio A)

Un solo modello XGBoost viene addestrato sui dati **concatenati** di tutti i
simboli configurati, invece di un modello per simbolo. Motivazione:

- Le 14 feature (`src/training/feature_engine.py`) sono già scale-invariant
  (RSI, rapporti, MACD normalizzato, ecc. — nessuna feature usa il prezzo
  assoluto), quindi pooling tra simboli con prezzi molto diversi (BTC ~62000,
  DOGE ~0.07) è tecnicamente valido.
- Più dati per il training complessivo, utile soprattutto per simboli con
  storico più corto.
- Il runtime (`src/inference/main.py`) carica già un solo modello condiviso
  per tutti i simboli — zero modifiche necessarie al caricamento.

**Attenzione a un errore facile da fare** (già commesso e corretto in questa
sessione): calcolare le feature (RSI/SMA/ATR/MACD, tutte operazioni su
finestre temporali) **dopo** aver concatenato i DataFrame grezzi di più
simboli genera valori falsi enormi ai confini tra un simbolo e l'altro (es.
un "return" calcolato tra l'ultimo prezzo di BTC e il primo di DOGE). Le
feature vanno sempre calcolate **per singolo simbolo**, e solo le feature già
pronte (non i prezzi grezzi) vanno concatenate.

## Split train/validation

Stesso principio vale per lo split: uno split temporale unico sul dataset già
concatenato (con `shuffle=False`, per non introdurre lookahead bias) finisce
per validare quasi solo sull'ultimo simbolo appeso alla lista, non su un
campione rappresentativo di tutti. `train_all_models.py` fa lo split
temporale (80/20) **per simbolo**, poi concatena separatamente le porzioni di
train e di validation.

Esempio concreto osservato in questa sessione: uno split fatto nell'ordine
sbagliato dava un confronto fuorviante challenger 42% vs champion 72%
(perché il validation set era quasi tutto SOL); con lo split corretto per
simbolo il confronto onesto è risultato 70.95% vs 70.78%.

## Logica champion/challenger

`Trainer.train(X_train, X_val, y_train, y_val)` (in `src/training/trainer.py`):

1. Addestra un `XGBClassifier` (iperparametri fissi: `n_estimators=100,
   max_depth=5, learning_rate=0.1` — nessun tuning automatico, non c'è
   nessuna libreria di hyperparameter search in `requirements.txt`).
2. Salva sempre il risultato come `config/models/challenger.pkl`.
3. Se esiste già un `champion.pkl`, lo valuta sullo **stesso** validation set
   e lo confronta per accuracy.
4. Solo se il challenger vince, viene promosso: `champion.pkl` viene
   sovrascritto (`shutil.copy`), e viene pubblicato `model_swap` su Redis
   (oggi non consumato da nessun listener — `inference/main.py` carica il
   modello solo all'avvio, va riavviato per usare il nuovo champion).

## Interpretare l'accuratezza

L'accuratezza riportata è sul validation set (20% più recente di ciascun
simbolo, non visto durante il training). Il target predetto è binario:
`(close.shift(-5) / close - 1) > 0.005` — cioè "il prezzo sale di più dello
0.5% nelle prossime 5 ore". Un'accuratezza intorno al 70% su questo target
non equivale a un win-rate di trading del 70%: la soglia di decisione buy/sell
in `inference/main.py` (`prob > 0.6` per buy, `prob < 0.4` per sell) è più
selettiva del semplice `pred == target`, e il PnL reale dipende anche da
SL/TP, leva e costi non modellati qui.

## Backup e rollback

Lo script non crea automaticamente un backup prima di sovrascrivere
`champion.pkl`. Prima di un training che potrebbe peggiorare il modello in
produzione, copia manualmente il file:

```bash
cp config/models/champion.pkl config/models/champion_backup_$(date +%Y%m%d).pkl
```

Per tornare indietro, ripristina la copia e riavvia Inference:

```bash
cp config/models/champion_backup_YYYYMMDD.pkl config/models/champion.pkl
./start.sh inference   # o dalla dashboard, pagina Controllo
```
