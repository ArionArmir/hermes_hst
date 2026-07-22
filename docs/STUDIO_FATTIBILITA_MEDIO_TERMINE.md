# Studio di fattibilità — Estensione al medio termine

**Data**: 2026-07-19 · **Stato**: DA APPROVARE (studio + piano)

---

## 0. Definizione, e una distinzione che decide metà dello studio

"Medio termine" = posizioni tenute **settimane-mesi**, orizzonte 1-3 anni.
Prima di parlare di strategie, la distinzione che l'industria confonde
volentieri:

- **Denaro che SERVE a medio termine** (spesa prevista fra 1-4 anni: auto,
  casa, cuscinetto oltre l'emergenza) → **non è investibile in asset
  rischiosi, punto**. L'azionario può stare sotto per 65-81 mesi (misurato);
  su un orizzonte di 2 anni il rischio di sequenza è dominante. Lo strumento
  giusto è il tasso privo di rischio (conto deposito, BOT/BTP corti, ~2.5-3.5%
  lordi oggi) e **nessuna piattaforma può migliorarlo**. Questo studio non ha
  nulla da offrire a quel denaro, e lo dice subito.
- **Strategie a frequenza media su capitale di lungo periodo** → è l'oggetto
  dello studio: esiste un gioco onesto lì?

## 1. Il vincolo fondamentale: la potenza statistica alle frequenze medie

La legge della piattaforma (`N = (2σ/μ)²`) tradotta in anni: per *dimostrare*
uno Sharpe annuo S servono `T ≈ 4/S²` anni di osservazioni indipendenti.

| Sharpe annuo vero | anni per dimostrarlo |
|---|---|
| 1.0 (eccellente) | ~4 |
| 0.5 (buono) | ~16 |
| 0.3 (tilt fattoriale realistico) | ~44 |

Conseguenze dure e non negoziabili:

1. **La validazione in avanti è infattibile** al medio termine: decisioni
   mensili producono ~12 osservazioni l'anno; un forward come quello lanciato
   stanotte sulla soglia 0.50 qui richiederebbe decenni.
2. **La validazione storica è possibile solo dove** la storia è lunga
   (azionario: 40+ anni mensili in casa ✓) o la larghezza è alta (47 crypto in
   sezione trasversale ✓). Crypto in serie temporale (5.5 anni): borderline.
3. **Il ML a orizzonte mensile su crypto è una macchina da overfitting per
   costruzione**: ~66 barre mensili contro 18+ feature. Non si propone.

## 2. Cosa dice l'evidenza esterna (con l'onestà sui suoi limiti)

- **Trend-following / time-series momentum**: l'unica anomalia di medio
  termine con un secolo di letteratura (Moskowitz-Ooi-Pedersen; AQR "A
  Century of Evidence"). Evidenza vera, MA: funziona come *portafoglio su
  decine di asset class* (tassi, materie prime, valute, indici) — accesso
  retail costoso; su un singolo asset è molto più debole; e il retail
  italiano paga il 26% a ogni uscita in utile, che la letteratura non conta.
- **Fattori azionari (value, quality, momentum)**: premi accademici reali,
  decaduti post-pubblicazione, con decenni di siccità (value 2010-2020).
  Per un PAC da 300 €/mese: complessità aggiunta per 0-2% incerti — verdetto
  onesto: non vale la candela rispetto all'indice puro.
- **Momentum cross-sectional crypto**: letteratura mista, debole post-2018.
  Mai testato da noi — ed è testabile coi dati in casa.
- **Carry strutturale**: GIÀ misurato da questa piattaforma (`carry_v1`,
  promosso, regime compresso). *È* la strategia di medio termine della casa:
  ribilanciamento settimanale, posizioni tenute settimane. Dorme per regime,
  non per bocciatura.

## 3. Fattibilità: cosa si può fare onestamente, coi dati in casa

### ✅ A. Il semaforo del carry (estensione dell'analista — descrittiva)

La strategia di medio termine promossa esiste già e dorme. Ciò che manca è
**sapere se il regime torna**: un pannello nel rapporto dell'analista che
mostra ogni mese il funding mediano trailing dei 46 simboli e il basis
trimestrale corrente, contro le soglie storiche (2021: ricco; 2025-26:
compresso/invertito). **Descrittivo**: l'eventuale attivazione del carry
resterebbe un atto separato con pre-registro proprio (il filtro di regime va
dichiarato prima, come da esito carry_v1). Costo: ore. Rischio metodologico:
zero.

### ✅ B. Test del trend sull'azionario (regola di Faber, 10-month MA)

*La* regola di timing di medio termine pubblicata (2007): dentro l'indice se
il prezzo è sopra la media mobile a 10 mesi, altrimenti in liquidità. Coi
nostri 40 anni mensili di S&P la potenza c'è (499 osservazioni, ~8 cicli).
Pre-registro con contabilità italiana vera: 26% sulle uscite in utile, tasso
risk-free quando fuori.

**Prior dichiarato**: riduce i drawdown, NON batte il buy&hold al netto delle
tasse italiane. **Valore del test**: qualunque esito, chiude *per misura* la
domanda "il medio termine può proteggere il mio PAC?" — se il prior regge,
l'IPS ne esce blindato con numeri; se cade, abbiamo trovato qualcosa che va
capito. Budget: ~4 tentativi. Costo: un pomeriggio.

### ✅ C. Momentum cross-sectional crypto (47 simboli, mensile)

Ogni mese: classifica per rendimento trailing, portafoglio dei migliori
contro i peggiori (e variante long-only). 66 mesi × 47 simboli = larghezza
sufficiente per un test onesto. **Prior dichiarato: H-nulla** (letteratura
debole post-2018, e sappiamo cosa fanno i costi). Budget: ~4 tentativi.
Costo: un pomeriggio. Dati: già in casa.

### ❌ Cosa NON è fattibile onestamente (e non va costruito)

- **Selezione discrezionale di medio termine** ("quali titoli/coin per i
  prossimi 6 mesi"): è il gioco 1 a frequenza più bassa — SPIVA, Bessembinder,
  senatori: tutto già visto. La piattaforma non farà da alibi quantitativo a
  questo gioco.
- **ML predittivo a orizzonte mensile su crypto**: punto 1.3 — overfitting
  garantito dalla scarsità di barre.
- **Un "consigliere di medio termine"** che dica cosa comprare: è la macchina
  della bontà con un altro vestito. Non può esistere onestamente.
- Qualunque cosa richieda dati a pagamento, finché le strade gratuite non
  sono esaurite.

## 4. Piano di aggiornamento proposto

| Fase | Cosa | Effort | Rischio metodologico |
|---|---|---|---|
| 1 | Semaforo del carry nel rapporto analista | ore | zero (descrittivo) |
| 2 | `PRE_REGISTRO_TREND.md` + test Faber su azionario (tasse ITA incluse) | mezza giornata | basso (pre-registrato, 40y di potenza) |
| 3 | `PRE_REGISTRO_MOMENTUM.md` + test cross-section crypto | mezza giornata | basso (pre-registrato, prior nullo) |

Ordine motivato: la 1 monitora l'unica strategia già promossa; la 2 ha la
potenza statistica migliore e protegge/blinda l'IPS; la 3 chiude l'ultima
famiglia di anomalie testabile gratis. Ogni fase: registro, gate, holdout
intatto, commit con autorizzazione.

**Aspettativa complessiva, dichiarata**: da 2 e 3 l'esito più probabile sono
due H-nulla che *aumentano* il valore della piattaforma — ogni porta chiusa
per misura è una tentazione futura in meno. Il medio termine "vivo" più
probabile resta il carry, e la fase 1 è la sveglia che aspetta il suo regime.

---

## Esito

*Da compilare a piano concluso.*

- [x] Fase 1: semaforo del carry live nel rapporto (prima lettura: regime
  NELLA NORMA al 31° percentile, in risalita dal fondo 2026H1)
- [x] Fase 2: Faber PERDE con significatività al netto delle tasse italiane
  (IC95 [−6.8%, −0.1%]) — l'IPS è blindato con numeri
- [x] Fase 3: momentum crypto H-nulla (diff +2.5% in IC ±33 punti)

**Verdetto dello studio**: il medio termine onesto per questa piattaforma è
UNO — il carry strutturale, monitorato dal semaforo, in attesa del suo
regime. Le porte del timing e della selezione a frequenza media sono chiuse
per misura. Registro: 154 tentativi.
