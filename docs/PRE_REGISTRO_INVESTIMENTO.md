# Pre-registro dell'investimento — Investment Policy Statement

**Data**: 2026-07-18 · **Titolare delle decisioni**: l'utente · **Stato**: DA APPROVARE
**Orizzonte**: ≥ 10 anni · **Revisione**: annuale, a calendario (luglio), mai su prezzo

---

## Perché questo documento esiste

È l'ultimo pre-registro della serie, e il primo con soldi veri. La logica è la
stessa dei 147 esperimenti: **le decisioni si scrivono a mente fredda, prima —
perché a mente calda non si decide: si reagisce.** L'unico alpha dimostrato e
recuperabile del gioco 2 è il behavior gap (~1,5-2%/anno perso dall'investitore
medio comprando dopo i rialzi e vendendo nei crolli): questo documento è lo
strumento con cui non pagarlo.

Le scelte qui dentro sono state prese dall'utente il 2026-07-18, dopo il test
dello stomaco sul simulatore di drawdown (`scripts/drawdown_simulator.py`),
con i numeri in euro davanti.

---

## Le decisioni

| Parametro | Scelta | Nota |
|---|---|---|
| **Allocazione** | **100% azionario indicizzato** (indice ampio: S&P 500 o azionario globale, via ETF UCITS ad accumulazione) | 0% crypto nel nucleo |
| **Versamento** | **300 €/mese**, stesso giorno ogni mese, in automatico se il broker lo consente | |
| **Versamento iniziale** | **Nessuno** | il capitale esistente (<10k€) resta liquido come fondo di emergenza |
| **Vendite ammesse** | SOLO per bisogni di vita reali o raggiungimento dell'obiettivo | mai su prezzo, notizia o previsione |
| **Ribilanciamento** | non necessario (un solo strumento) — se in futuro entrerà un satellite: soglia ±5 punti sui pesi, verificata una volta l'anno | |

*Nota strumento*: la simulazione usava l'S&P 500 come proxy; la scelta
dell'ETF concreto (S&P 500 o world, purché indicizzato, ad accumulazione, a
basso costo — TER ≤ 0,2%) spetta all'utente presso il proprio broker. Nessun
prodotto specifico è raccomandato da questo documento.

## Il test dello stomaco — attestazione

L'utente ha letto, in euro, il mese per mese dell'episodio peggiore della
propria allocazione (Covid 2020: −19%, "versati 6.400 € | il conto segna
6.193 €") **e il listino vero dell'azionario**:

> **2008: −53%, 65 mesi sotto il picco. Dot-com: −46%, 81 mesi.**

La firma su questo documento significa: *"so che prima o poi il conto segnerà
meno della metà del suo massimo, per anni, e il piano prevede che io continui
a versare 300 € anche in quei mesi — **specialmente** in quei mesi, perché il
DCA compra di più quando i prezzi sono bassi (test:
`test_dca_compra_di_piu_quando_il_prezzo_scende`)."*

## Il protocollo del crollo

Quando arriverà (arriverà), nell'ordine:

1. **Rileggere questa pagina**, in particolare l'attestazione qui sopra.
2. **Non aprire il conto più del solito.** Il controllo del portafoglio è
   ammesso al massimo una volta al mese; nei crolli la tentazione raddoppia e
   la regola resta.
3. **Il bonifico parte regolare.** Se c'è liquidità extra, è ammesso portare
   il mensile fino a 2× (600 €) — mai ridurlo per paura.
4. **Nessuna vendita, nessun cambio di allocazione.** I cambi di allocazione
   richiedono la procedura di emendamento (sotto), che ha 30 giorni di attesa
   proprio per essere inservibile nel panico.

## Il budget satellite — lo sfogo dimensionato

La voglia di "selezionare progetti promettenti" non sparirà. Regole:

- **Tetto: 10% del valore del portafoglio**, mai di più.
- Ogni posizione satellite entra SOLO con una **tesi pre-registrata** nel
  registro (sotto) *prima* dell'acquisto: cosa, perché, cosa la
  falsificherebbe, taglia, orizzonte.
- Le posizioni satellite si misurano onestamente contro il nucleo: fra 3 anni
  il registro dirà quanto valgono le selezioni dell'utente — dati, non
  ricordi selettivi.
- Oggi il satellite è **vuoto** (scelta esplicita: 0% crypto).

## Il registro di aderenza

File: `docs/investment_adherence.jsonl` (stesso principio del registro
esperimenti: append-only, si scrive tutto, anche ciò che non lusinga).

Si registrano: ogni versamento saltato o modificato (con perché), ogni
acquisto fuori piano, ogni vendita, ogni tesi satellite, e la revisione
annuale. **La metrica del piano non è il rendimento — è l'aderenza**: il
rendimento lo fa il mercato, l'aderenza la fa l'utente, e solo la seconda è
sotto il suo controllo.

## Emendamenti

- Qualunque modifica va **scritta qui prima di essere eseguita**.
- Modifiche al mensile: libere se motivate da cambi di reddito (registrate).
- Modifiche all'allocazione: richiedono di rifare il test dello stomaco sul
  simulatore **più 30 giorni di attesa** tra la scrittura e l'esecuzione.
  L'attesa è il punto: un'allocazione che non può aspettare 30 giorni è una
  reazione, non una decisione.

## Aspettative oneste — scritte prima, per non riscriverle dopo

- Rendimento reale storico dell'azionario ampio: ~5-7%/anno nominale su
  decenni, **non ogni anno** — con anni a −20/−50% dentro.
- Ordine di grandezza (a ~6%/anno, NON una promessa): 300 €/mese →
  ~36.000 € versati in 10 anni ≈ 49.000 €; ~72.000 € in 20 anni ≈ 138.000 €.
- **Questo piano non è reddito.** L'obiettivo dei 7.200 €/anno vive
  nell'altro binario (competenze/freelance) e i due non vanno mescolati:
  chiedere reddito al portafoglio è il modo in cui i piani si rompono.

---

## Esito della revisione annuale

*Da compilare ogni luglio: aderenza (versamenti fatti/saltati), deviazioni
registrate, eventuale emendamento. NON si compila il rendimento come
giudizio: si annota e basta.*

- [ ] Luglio 2027: __
