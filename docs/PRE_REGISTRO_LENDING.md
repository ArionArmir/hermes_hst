# Pre-registro — Lending di stablecoin (Aave v3, passivo)

**Data**: 2026-07-18 · **Stato**: DA APPROVARE · **Budget dichiarato**: 2 configurazioni

---

## Il gioco

Depositare stablecoin su Aave e incassare il tasso variabile di supply. Chi
paga: i borrower — in maggioranza trader che prendono leva contro collaterale.
È **pura vendita di capitale**: nessuna strategia, nessun parametro, nessun
timing. Il gioco più passivo della lista dopo la riga zero stessa.

La domanda dell'esperimento è una sola: **il tasso batte il T-bill abbastanza
da pagare i rischi che il T-bill non ha?**

## Dati — verificati prima di scrivere (2026-07-18)

DefiLlama yields API (gratuita): APY giornaliero dei pool Aave v3 Ethereum —
USDT (TVL $613M, il mercato più grande) e USDC ($199M) — **dal 2023-02**,
~1.255 punti giornalieri ciascuno. I pool Aave v2 (storia 2020-22) non sono
più esposti dall'API.

**Limite dichiarato della finestra**: 3.45 anni che NON includono né la DeFi
summer 2021 (tassi a doppia cifra) né i crolli 2022 (Terra, FTX). La finestra
copre solo il regime maturo — meno distorta verso l'alto, ma cieca sugli
estremi in entrambe le direzioni.

**Gate**: ≥ 3.0 anni di dati giornalieri per entrambi gli asset, o STOP.

## Il prior di chi scrive, dichiarato prima

**H-nulla attesa: eccesso sul T-bill ≈ 0 o negativo per gran parte della
finestra.** Nel 2023-2024 il T-bill rendeva ~5% e i tassi DeFi stavano spesso
sotto — è il motivo per cui sono esplosi i Treasury tokenizzati. L'eccesso
positivo vive probabilmente solo nei picchi di domanda di leva (bull 2024),
cioè nello stesso regime che pagava funding e basis. Se così, sarà la **terza
misura indipendente della stessa cosa**: tutti questi "rendimenti crypto"
sono facce della domanda di leva long, e si muovono insieme.

## Configurazioni

| # | Asset | Ruolo |
|---|---|---|
| 1 | **USDT** (Aave v3 Ethereum, TVL maggiore) | **PRIMARIA** — regola meccanica: il mercato più grande |
| 2 | USDC | controllo |

## Contabilità — dichiarata prima

Rendimento mensile = capitalizzazione dei tassi giornalieri di supply.
Costi: gas trascurabile per taglie ≥ $10k, entrata/uscita senza slippage
(mint/burn 1:1) — dichiarato zero. **Eccesso mensile = rendimento − T-bill 3M
dello stesso mese** (FRED DTB3, stessa fonte del basis).

## Cosa il backtest NON cattura — dichiarato, ed è il cuore del gioco

- **Exploit del protocollo**: Aave non è mai stato violato, ma il rischio non
  è zero e un singolo evento costa il capitale, non il rendimento.
- **Depeg dello stablecoin** — per la primaria è il rischio dominante: USDT.
- **Rischio oracle/governance**, congelamenti, upgrade.

Per questo la soglia operativa è sull'**eccesso**, non sul rendimento: un
lending che rende quanto il T-bill con questi rischi in più è un gioco perso
per definizione.

## Criterio di successo — dichiarato prima di guardare

Sulla **primaria** (USDT), serie mensile dell'**eccesso sul T-bill**. In AND:

1. **Bootstrap sui mesi: IC 95% dell'eccesso annualizzato > 0** — la riga
   zero stavolta è dentro il gate primario.
2. **DSR > 90% con `n_trials = 2`** (questa famiglia) sugli eccessi mensili.
3. **Eccesso medio annualizzato ≥ +2%** — la soglia che paga il rischio di
   coda non modellato (exploit, depeg). Più bassa del 3% del carry perché
   qui non c'è rischio di margine né rotazione, ma non zero: il capitale è
   interamente esposto al protocollo.
4. **Mesi con eccesso positivo ≥ 55%.**

DSR cumulativo (`n_trials = 147`) riportato, non vincolante (motivazione
opzione C, a verbale nel pre-registro carry).

## Registro

Famiglia `lending_v1`. **Conteggio: 145 + 2 = 147.**

---

## Esito

*Da compilare a run concluso.*

- [ ] Gate: __ anni per asset
- [ ] Primaria — eccesso ann.: __ · IC95: __ · mesi+: __
- [ ] Spaccato annuale: __
- [ ] Promuovibile: sì/no