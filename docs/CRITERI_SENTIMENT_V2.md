# Criteri di accettazione — Sentiment v2 (in ombra)

**Data**: 2026-07-20 · **Stato**: IN VALIDAZIONE · **Punto di decisione**: 2026-08-04 (14 giorni di ombra)

---

## Perché esiste

La v1 ha tre difetti misurati il 2026-07-20: ~60% di cicli tutti-zero
inspiegati (modello da 1.5B *per codice* che sputa zeri su JSON valido),
output degeneri a scala aritmetica (-0.80, -0.70, ...), e ri-valutazione
ogni 5 minuti delle stesse notizie RSS senza nozione di novità. Il veto del
motore (-0.5) è di fatto spento la maggior parte del tempo.

## Il vincolo che governa tutto

**La v2 non tocca il motore.** Chiavi Redis separate (`sentiment_v2*`),
nessuna pubblicazione sui canali che il motore ascolta (`sentiment_update`,
`sentiment_asset`): forward_v1 resta intatto. Lo switch, se mai, avviene al
punto di decisione — non prima.

## Cosa cambia nella v2

1. **Novità come concetto**: memoria dei titoli visti (hash + prima
   apparizione); il modello valuta solo notizie nuove. Senza novità, il
   punteggio decade verso 0 con mezza vita 6 ore. Con novità: media 50/50
   tra punteggio decaduto e punteggio delle notizie nuove (una notizia
   singola non azzera la memoria).
2. **Modello adatto**: qwen2.5:7b-instruct (locale, gratuito), una chiamata
   per asset — il prompt monolitico a 7 asset è la causa delle scale.
3. **Telemetria onesta**: ogni punteggio ha uno stato dichiarato
   (`nuovo | decaduto | senza_notizie | errore | degenere`): mai più uno
   zero ambiguo.
4. **Sonda di ripetibilità**: 1 chiamata su 20 viene duplicata sullo stesso
   input e registrata: la stabilità si misura, non si presume.

## Criteri (scritti prima, giudicati al punto di decisione)

| Criterio | v1 misurata | Soglia v2 |
|---|---|---|
| Cicli tutti-zero non spiegati dalla telemetria | ~60% | < 5% |
| Output degeneri non intercettati dalla guardia | frequenti | 0 |
| Ripetibilità (sonda doppia): stesso segno e scarto ≤ 0.2 | ignota | ≥ 80% delle sonde |
| Punteggi con stato dichiarato | 0% | 100% |

## I tre esiti possibili al 2026-08-04 (dichiarati oggi)

- **PASSA + si vuole la situazione aggiornata** → terminare forward_v1
  registrando l'esito parziale, agganciare v2, ripartire con forward_v2 e
  pre-registro fresco.
- **PASSA ma non si interrompe** → v2 in panchina fino al verdetto forward.
- **NON PASSA** → forward_v1 mai disturbato; la conclusione misurata è che
  il sentiment locale gratuito non regge, e resta agli atti.

Il confronto NON valuta capacità predittiva: quella sarebbe ricerca e
richiederebbe un pre-registro suo.
