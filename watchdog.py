#!/usr/bin/env python3
"""
Watchdog degli heartbeat: legge da Redis i segni di vita dei processi
(heartbeat_* e last_tick_*), verifica che Ollama risponda su HTTP e
notifica via Telegram/email quando qualcosa è fermo oltre soglia.
Pensato per girare da cron ogni minuto:

    * * * * * cd /home/alexbi/hermes_hft && venv/bin/python watchdog.py >> logs/watchdog.log 2>&1

Anti-spam: lo stato "già allertato" vive nel set Redis `watchdog_alerted` —
un problema notifica UNA volta sola, poi tace finché non rientra (e al
rientro manda una notifica di recovery). Se Redis stesso è giù, il dedup usa
un file marker locale (logs/.watchdog_redis_down).

Con --restart prova anche a riavviare i processi con heartbeat fermo
(stop+start via dashboard/utils/process_manager, lo stesso della dashboard).
Di default NON riavvia nulla: un riavvio automatico durante uno stop
intenzionale farebbe più danni di un alert in più.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

# Soglie in secondi. Heartbeat: engine/inference li scrivono ogni ~5s,
# sentiment ogni 15s. Tick: su Binance Futures BTC/ETH scambiano di continuo,
# nessun tick per 3 minuti = WebSocket morto, non mercato fermo. Candele:
# preavviso MOLTO prima del cutoff interno di CandleFeed (2h di default per
# candele 1h) — un processo vivo (heartbeat OK) può comunque tradare su dati
# di mercato stantii se Binance REST è irraggiungibile (docs/IMPROVEMENT_PLAN.md, V2).
CHECKS = {
    "engine": {"key": "heartbeat_engine", "stale_after": 120},
    "inference": {"key": "heartbeat_inference", "stale_after": 120},
    "sentiment": {"key": "heartbeat_sentiment", "stale_after": 120},
    "carry": {"key": "heartbeat_carry", "stale_after": 7800},
    "liquidations": {"key": "heartbeat_liquidations", "stale_after": 400},
    # Heartbeat ≠ dati: il 2026-07-19 lo stream forceOrder è rimasto muto 11
    # ore (migrazione endpoint Binance) col heartbeat verde. Le liquidazioni
    # market-wide non stanno mai ferme un'ora: silenzio = stream rotto.
    "eventi liquidazioni": {"key": "last_liquidation_event", "stale_after": 3600},
    "liquidations bybit": {"key": "heartbeat_liquidations_bybit", "stale_after": 400},
    "sentiment v2": {"key": "heartbeat_sentiment_v2", "stale_after": 120},
    "eventi liq. bybit": {"key": "last_liquidation_event_bybit", "stale_after": 3600},
    "tick engine": {"key": "last_tick_engine", "stale_after": 180},
    "tick inference": {"key": "last_tick_inference", "stale_after": 180},
    "candele": {"key": "candle_feed_last_success", "stale_after": 900},
}
RESTARTABLE = ("engine", "inference", "sentiment", "carry", "liquidations")
ALERT_STATE_KEY = "watchdog_alerted"
REDIS_DOWN_MARKER = REPO_ROOT / "logs" / ".watchdog_redis_down"
OLLAMA_DEFAULT_URL = "http://localhost:11434"

# Model-health monitor (docs/IMPROVEMENT_PLAN.md, V4/N4): finestra di trade
# recenti su cui giudicare, sotto questa soglia il campione è troppo piccolo
# per un giudizio statistico. Le soglie assolute sono deliberatamente larghe
# (un floor sotto il quale qualcosa è chiaramente andato storto), non la
# taratura fine — quella resta compito di tune_strategy.py/walk_forward.py.
MODEL_HEALTH_WINDOW = 20
MODEL_HEALTH_MIN_TRADES = 10
MODEL_HEALTH_HIT_RATE_FLOOR = 0.30


def load_env(path: Path = REPO_ROOT / ".env"):
    """Cron non passa da start.sh: carichiamo .env a mano (serve al Notifier
    per TELEGRAM_TOKEN/CHAT_ID). setdefault: l'ambiente esistente vince."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def evaluate_checks(redis_values: Dict[str, Optional[str]],
                    now: datetime) -> Dict[str, Optional[str]]:
    """{nome check: descrizione problema o None se sano}."""
    problems: Dict[str, Optional[str]] = {}
    for name, spec in CHECKS.items():
        raw = redis_values.get(spec["key"])
        if not raw:
            problems[name] = "nessun heartbeat registrato su Redis"
            continue
        try:
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            problems[name] = f"timestamp illeggibile: {raw!r}"
            continue
        age = (now - ts).total_seconds()
        if age > spec["stale_after"]:
            problems[name] = f"fermo da {age:.0f}s (soglia {spec['stale_after']}s)"
        else:
            problems[name] = None
    return problems


def check_ollama(base_url: Optional[str] = None) -> Optional[str]:
    """None se Ollama risponde, descrizione del problema altrimenti.
    Serve perché il processo sentiment DEGRADA senza errori fatali quando
    Ollama è giù (pubblica punteggi neutri 0) e nessun heartbeat lo rivela:
    successo il 2026-07-15 — un'intera giornata di sentiment a zero dopo un
    riavvio di WSL con la unit ollama disabilitata, senza alcun alert."""
    url = (base_url or os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_URL)).rstrip("/") + "/api/version"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return None
        return f"HTTP {resp.status_code} da {url}"
    except requests.RequestException as e:
        return f"non raggiungibile ({url}): {e.__class__.__name__}"


def check_config_drift(redis_client,
                       manifest_path: Optional[Path] = None) -> Optional[str]:
    """None se lo stato vivo coincide col manifest dichiarato
    (config/forward_manifest.yaml): TUTTI i campi della config contro Redis,
    più lo sha256 del champion su disco. Esiste per il 2026-07-20: un
    riavvio di Redis ha resuscitato la soglia pre-esperimento (0.55 invece
    di 0.50) e il forward ha girato ~20h fuori protocollo senza che nulla lo
    segnalasse (docs/PRE_REGISTRO_FORWARD.md, registro incidenti). Il check
    del modello copre anche il trainer settimanale rotto: se sovrascrivesse
    champion.pkl, l'inference lo ricaricherebbe a caldo in silenzio."""
    import hashlib
    import json

    import yaml
    manifest_path = manifest_path or REPO_ROOT / "config" / "forward_manifest.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
        raw = redis_client.get("trading_config")
    except Exception as e:
        return f"manifest non confrontabile: {e}"

    derive = []
    if raw:                          # Redis vuoto: al prossimo avvio vince il YAML
        live = json.loads(raw)
        dichiarata = manifest["config"]
        derive += [f"{campo}: live={live[campo]!r} dichiarato={dichiarata[campo]!r}"
                   for campo in dichiarata
                   if campo in live and live[campo] != dichiarata[campo]]

    modello = REPO_ROOT / "config" / "models" / "champion.pkl"
    try:
        sha = hashlib.sha256(modello.read_bytes()).hexdigest()
        if sha != manifest["champion_sha256"]:
            derive.append(f"champion.pkl: sha {sha[:12]} ≠ dichiarato "
                          f"{manifest['champion_sha256'][:12]} — modello cambiato "
                          "a esperimento in corso")
    except OSError as e:
        derive.append(f"champion.pkl illeggibile: {e}")
    return "; ".join(derive) if derive else None


def check_model_health(redis_client) -> Optional[str]:
    """None se il comportamento recente del modello è nella norma,
    descrizione del problema altrimenti. Non aziona nulla (il circuit
    breaker già ferma le nuove aperture in una crisi acuta): rende visibile
    un degrado GRADUALE che il circuit breaker da solo non intercetta —
    il fold catastrofico del walk-forward era proprio questo, una sequenza
    di trade in perdita scoperta solo guardando la dashboard a posteriori."""
    from src.shared import store
    trades = store.read_trades(limit=MODEL_HEALTH_WINDOW)
    if len(trades) < MODEL_HEALTH_MIN_TRADES:
        return None

    hit_rate = float((trades["pnl"] > 0).mean())
    net_pnl = float(trades["pnl"].sum())
    # Combinare le due condizioni riduce i falsi positivi: un hit rate basso
    # con PnL netto comunque positivo (poche vincite grandi, molte piccole
    # perdite) può essere una strategia sana, non un modello che decade.
    if not (hit_rate < MODEL_HEALTH_HIT_RATE_FLOOR and net_pnl < 0):
        return None

    detail = f"hit rate {hit_rate:.0%} e PnL netto {net_pnl:+.2f} USDT sugli ultimi {len(trades)} trade"
    expected = redis_client.get("champion_hit_rate")
    if expected:
        try:
            detail += f" (il champion prometteva {float(expected):.0%} in validation)"
        except ValueError:
            pass
    return detail


def split_transitions(previously_alerted: Set[str],
                      problems: Dict[str, Optional[str]]) -> Tuple[Dict[str, str], List[str]]:
    """(nuovi problemi da notificare, check rientrati da notificare come
    recovery). I problemi già notificati in un giro precedente non tornano."""
    new_alerts = {name: desc for name, desc in problems.items()
                  if desc and name not in previously_alerted}
    recovered = sorted(name for name in previously_alerted if problems.get(name) is None)
    return new_alerts, recovered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true",
                        help="riavvia i processi con heartbeat fermo (default: solo notifica)")
    args = parser.parse_args()

    load_env()
    import redis
    from src.shared.notifier import Notifier
    notifier = Notifier()  # istanziato DOPO load_env, l'istanza globale del modulo leggerebbe env vuote

    client = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                         port=int(os.getenv("REDIS_PORT", "6379")),
                         decode_responses=True)
    try:
        client.ping()
    except redis.RedisError as e:
        print(f"[watchdog] Redis irraggiungibile: {e}")
        if not REDIS_DOWN_MARKER.exists():
            REDIS_DOWN_MARKER.parent.mkdir(exist_ok=True)
            REDIS_DOWN_MARKER.touch()
            notifier.notify_error("Watchdog: Redis irraggiungibile — l'intero sistema è probabilmente fermo")
        return 2
    if REDIS_DOWN_MARKER.exists():
        REDIS_DOWN_MARKER.unlink()
        notifier.send_telegram("✅ Watchdog: Redis di nuovo raggiungibile")

    now = datetime.now(timezone.utc)
    keys = [spec["key"] for spec in CHECKS.values()]
    values = dict(zip(keys, client.mget(keys)))
    problems = evaluate_checks(values, now)
    problems["ollama"] = check_ollama()
    problems["modello"] = check_model_health(client)
    problems["config drift"] = check_config_drift(client)
    previously_alerted = set(client.smembers(ALERT_STATE_KEY))
    new_alerts, recovered = split_transitions(previously_alerted, problems)

    if new_alerts:
        lines = "\n".join(f"• {name}: {desc}" for name, desc in sorted(new_alerts.items()))
        print(f"[watchdog] PROBLEMI:\n{lines}")
        notifier.notify_error(f"Watchdog Hermes:\n{lines}")
        client.sadd(ALERT_STATE_KEY, *new_alerts.keys())

    if recovered:
        names = ", ".join(recovered)
        print(f"[watchdog] rientrati: {names}")
        notifier.send_telegram(f"✅ Watchdog Hermes: rientrato — {names}")
        client.srem(ALERT_STATE_KEY, *recovered)

    # il feed eventi deriva dagli artefatti: un suo errore non deve mai
    # zittire il watchdog, che è l'ultima linea di difesa
    try:
        from src.eventi.osservatore import osserva_tutto
        osserva_tutto(new_alerts, recovered)
    except Exception as e:
        print(f"[watchdog] osservatore eventi fallito (non bloccante): {e}")

    if args.restart:
        stale_services = [name for name, desc in problems.items()
                          if desc and name in RESTARTABLE]
        if stale_services:
            sys.path.insert(0, str(REPO_ROOT / "dashboard"))
            from utils import process_manager
            for service in stale_services:
                process_manager.stop(service)
                ok, msg = process_manager.start(service)
                print(f"[watchdog] riavvio {service}: {msg}")
                notifier.send_telegram(f"🔄 Watchdog: riavvio {service} → {msg}")

    active_problems = {name: desc for name, desc in problems.items() if desc}
    if not active_problems:
        print(f"[watchdog] {now.isoformat()} tutti i controlli OK")
    return 1 if active_problems else 0


if __name__ == "__main__":
    sys.exit(main())
