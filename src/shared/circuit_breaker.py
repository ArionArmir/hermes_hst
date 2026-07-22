"""
Circuit breaker di portafoglio: sospende le NUOVE aperture (mai le chiusure —
SL/TP/reverse restano sempre attivi) quando la sequenza recente di trade
segnala un problema strutturale, non un singolo trade sfortunato.

Nasce da un fatto misurato, non da un'ipotesi: il fold peggiore del
walk-forward (−33 USDT) non era causato da troppe posizioni correlate aperte
insieme (il picco era di sole 3, già dentro il cap direzionale) ma da 6 stop
loss CONSECUTIVI in ~15 ore — il modello che sbaglia ripetutamente in un
regime di mercato specifico. Il cap direzionale è cieco al tempo; questo
modulo non lo è (docs/IMPROVEMENT_PLAN.md, V1).

Tre condizioni indipendenti, ciascuna con una risposta diversa:
- perdite consecutive: N trade chiusi in perdita di fila → pausa TEMPORANEA
  con cooldown (il modello potrebbe tornare a funzionare da solo);
- perdita giornaliera: PnL chiuso nel giorno UTC corrente sotto una soglia %
  del capitale a inizio giornata → pausa fino al giorno UTC successivo;
- drawdown dal picco: capitale sotto una soglia % rispetto al massimo mai
  raggiunto → pausa PERSISTENTE, richiede reset manuale (stesso principio
  del pulsante "Reset posizioni" già in dashboard: un evento di questa
  gravità va rivisto da un umano, non ripartire da solo).

Riusato SIA dall'engine live SIA da backtest_joint: stessa classe, stesso
comportamento in entrambi i contesti — altrimenti il backtest tarerebbe una
versione della strategia diversa da quella davvero tradata.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd


@dataclass
class CircuitBreakerParams:
    # Tarati con walk_forward.py sweep 4 fold (2026-07-16): un cooldown
    # breve (60-360 min) non protegge affatto dal fold catastrofico — il
    # "regime cattivo" lì durava ~15 ore, il sistema riapriva mentre
    # persisteva ancora. Solo un cooldown ≥ 12h lo attraversa con margine:
    # consec=3/cooldown=1440 è risultato il migliore su tutti e 4 i fold
    # (fold peggiore: −33 → +2, totale: −23 → +12; fold buoni invariati).
    max_consecutive_losses: Optional[int] = 3
    consecutive_loss_cooldown_minutes: int = 1440
    max_daily_loss_pct: Optional[float] = 0.05   # frazione del capitale a inizio giornata UTC
    max_drawdown_pct: Optional[float] = 0.20     # frazione dal picco di equity mai raggiunto

    @staticmethod
    def from_config(config: dict) -> Optional["CircuitBreakerParams"]:
        """None se circuit_breaker_enabled è false in config: stesso
        schema YAML usato dall'engine, riletto qui per i CLI di taratura/
        validazione (tune_strategy.py, walk_forward.py, run_backtest.py) —
        senza questo, backtest e live potrebbero tarare/validare con
        protezioni diverse da quelle davvero attive in produzione."""
        if not config.get("circuit_breaker_enabled", True):
            return None
        return CircuitBreakerParams(
            max_consecutive_losses=config.get("circuit_breaker_max_consecutive_losses", 3),
            consecutive_loss_cooldown_minutes=config.get("circuit_breaker_cooldown_minutes", 1440),
            max_daily_loss_pct=config.get("circuit_breaker_max_daily_loss_pct", 0.05),
            max_drawdown_pct=config.get("circuit_breaker_max_drawdown_pct", 0.20),
        )


class CircuitBreaker:
    def __init__(self, params: Optional[CircuitBreakerParams] = None):
        self.params = params or CircuitBreakerParams()
        self._consecutive_losses = 0
        self._tripped_until: Optional[datetime] = None
        self._trip_reason: Optional[str] = None
        self._current_day: Optional[str] = None
        self._day_start_capital: Optional[float] = None
        self._daily_trip = False
        self._peak_capital: Optional[float] = None
        self._drawdown_trip = False

    def update_params(self, params: CircuitBreakerParams):
        """Aggiorna la configurazione preservando lo stato interno (contatori,
        picco, trip in corso) — usato dal reload di config a caldo, che non
        deve azzerare una pausa già in corso."""
        self.params = params

    def record_trade(self, pnl: float, capital_after: float, now: Optional[datetime] = None):
        """Da chiamare a OGNI chiusura di posizione (non solo quando il
        breaker è già attivo): aggiorna lo stato usato dai check di trip."""
        now = now or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if day != self._current_day:
            self._current_day = day
            self._day_start_capital = capital_after - pnl
            self._daily_trip = False

        self._consecutive_losses = self._consecutive_losses + 1 if pnl < 0 else 0

        if (self.params.max_consecutive_losses is not None
                and self._consecutive_losses >= self.params.max_consecutive_losses):
            self._tripped_until = now + timedelta(minutes=self.params.consecutive_loss_cooldown_minutes)
            self._trip_reason = (
                f"{self._consecutive_losses} perdite consecutive "
                f"(cooldown {self.params.consecutive_loss_cooldown_minutes} min)"
            )

        if self.params.max_daily_loss_pct is not None and self._day_start_capital:
            daily_pct = (capital_after - self._day_start_capital) / self._day_start_capital
            if daily_pct <= -self.params.max_daily_loss_pct:
                self._daily_trip = True
                self._trip_reason = (
                    f"perdita giornaliera {daily_pct:.1%} (max {-self.params.max_daily_loss_pct:.1%})"
                )

        self._update_drawdown(capital_after)

    def _update_drawdown(self, capital: float):
        if self._peak_capital is None or capital > self._peak_capital:
            self._peak_capital = capital
        if self.params.max_drawdown_pct is not None and self._peak_capital:
            drawdown = (capital - self._peak_capital) / self._peak_capital
            if drawdown <= -self.params.max_drawdown_pct:
                self._drawdown_trip = True
                self._trip_reason = (
                    f"drawdown {drawdown:.1%} dal picco (max {-self.params.max_drawdown_pct:.1%})"
                )

    def is_tripped(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        # revisione 2026-07-21 (E1): il trip giornaliero deve rientrare al
        # cambio di giorno UTC anche se NESSUN trade si chiude nel frattempo.
        # Prima rientrava solo dentro record_trade: se scattava all'ultima
        # chiusura, restava attivo per sempre (blocca le aperture → niente
        # più chiusure → mai più record_trade).
        if self._daily_trip and now.strftime("%Y-%m-%d") != self._current_day:
            self._daily_trip = False
            if not self._drawdown_trip and self._tripped_until is None:
                self._trip_reason = None      # niente più "perdita giornaliera" con tripped=False
        if self._drawdown_trip or self._daily_trip:
            return True
        if self._tripped_until is not None:
            if now < self._tripped_until:
                return True
            # Cooldown scaduto: si autoresetta, il modello ha diritto a
            # un'altra possibilità senza intervento umano.
            self._tripped_until = None
            self._consecutive_losses = 0
        return False

    def status(self, now: Optional[datetime] = None) -> dict:
        return {
            "tripped": self.is_tripped(now),
            "reason": self._trip_reason,
            "consecutive_losses": self._consecutive_losses,
            "drawdown_trip": self._drawdown_trip,
            "daily_trip": self._daily_trip,
            "cooldown_until": self._tripped_until.isoformat() if self._tripped_until else None,
        }

    def manual_reset(self):
        """Reset manuale completo (pulsante dashboard): azzera anche i trip
        persistenti (drawdown/giornaliero), non solo il cooldown temporale.
        Il picco riparte dal capitale attuale, non dal vecchio già superato."""
        self._consecutive_losses = 0
        self._tripped_until = None
        self._trip_reason = None
        self._daily_trip = False
        self._drawdown_trip = False
        self._peak_capital = None

    def seed_from_history(self, trades_df: Optional[pd.DataFrame], current_capital: float,
                          now: Optional[datetime] = None,
                          reset_after: Optional[str] = None,
                          reset_capital: Optional[float] = None):
        """Ricostruisce lo stato dallo storico dei trade dopo un riavvio.
        Senza questo, un crash durante una serie di perdite (con systemd
        Restart=always) azzererebbe il contatore a ogni riavvio.

        reset_after/reset_capital (revisione 2026-07-21, E2): se c'è stato un
        reset manuale, i trade PRECEDENTI vanno ignorati e il picco riparte
        dal capitale al reset — altrimenti ogni riavvio ripescherebbe il
        vecchio picco dallo storico e ri-armerebbe il trip che l'umano aveva
        appena azzerato (deadlock: reset necessario a ogni boot)."""
        now = now or datetime.now(timezone.utc)
        if trades_df is not None and not trades_df.empty and reset_after:
            trades_df = trades_df[trades_df["timestamp"].astype(str) >= reset_after]
        if trades_df is None or trades_df.empty:
            self._peak_capital = reset_capital if reset_capital is not None else current_capital
            return

        trades_df = trades_df.sort_values("timestamp")
        # il picco è il massimo capital_after DA (eventuale) reset in poi:
        # con reset_capital come base, i trade filtrati sono già solo i
        # successivi, quindi il max riflette solo la storia post-reset
        candidati = []
        if reset_capital is not None:
            candidati.append(reset_capital)
        if "capital_after" in trades_df and trades_df["capital_after"].notna().any():
            candidati.append(float(trades_df["capital_after"].max()))
        if candidati:
            self._peak_capital = max(candidati)

        consecutive = 0
        for pnl in trades_df["pnl"].iloc[::-1]:
            if pnl < 0:
                consecutive += 1
            else:
                break
        self._consecutive_losses = consecutive

        last_ts = pd.to_datetime(trades_df["timestamp"].iloc[-1])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        self._current_day = last_ts.strftime("%Y-%m-%d")
        today_trades = trades_df[
            pd.to_datetime(trades_df["timestamp"]).dt.strftime("%Y-%m-%d") == self._current_day
        ]
        if not today_trades.empty:
            first_today = today_trades.iloc[0]
            self._day_start_capital = float(first_today["capital_after"]) - float(first_today["pnl"])
            # revisione 2026-07-21 (E1b): ricostruisci anche il trip giornaliero
            # — un crash a metà giornata perdente non deve azzerare la protezione
            if self.params.max_daily_loss_pct is not None and self._day_start_capital:
                daily_pct = (current_capital - self._day_start_capital) / self._day_start_capital
                if daily_pct <= -self.params.max_daily_loss_pct:
                    self._daily_trip = True
                    self._trip_reason = f"perdita giornaliera {daily_pct:.1%} (ricostruita dopo riavvio)"

        # Cooldown da perdite consecutive: replay dell'INTERA storia (post
        # eventuale reset), non solo delle perdite in coda. In live ogni perdita
        # col contatore >= soglia ri-arma _tripped_until, e una vittoria azzera
        # il contatore ma NON cancella un cooldown già in corso (is_tripped lo
        # smonta solo alla scadenza). Ricostruire dalle sole perdite finali
        # perdeva il cooldown quando l'ultimo trade chiuso era una vittoria,
        # riaprendo il trading fino a un giorno troppo presto dopo un riavvio.
        if self.params.max_consecutive_losses is not None:
            cooldown = timedelta(minutes=self.params.consecutive_loss_cooldown_minutes)
            sim = 0
            armato_fino = None
            for ts, pnl in zip(pd.to_datetime(trades_df["timestamp"]), trades_df["pnl"]):
                if pnl < 0:
                    sim += 1
                    if sim >= self.params.max_consecutive_losses:
                        if ts.tzinfo is None:
                            ts = ts.tz_localize("UTC")
                        armato_fino = ts.to_pydatetime() + cooldown
                else:
                    sim = 0
            # _tripped_until impostato incondizionatamente: è is_tripped a
            # decidere la scadenza (e ad auto-resettarsi se già passata),
            # coerente con il resto del modulo e col now passato dal chiamante.
            if armato_fino is not None:
                self._tripped_until = armato_fino
                self._trip_reason = (
                    f"cooldown perdite consecutive attivo fino a "
                    f"{armato_fino:%Y-%m-%d %H:%M} UTC "
                    "(ricostruito da storico dopo riavvio)"
                )

        self._update_drawdown(current_capital)
