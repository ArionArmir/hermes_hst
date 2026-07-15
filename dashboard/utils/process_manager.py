"""
Avvio/arresto/stato dei processi del bot (engine, inference, sentiment).

Due backend, scelti automaticamente:
- systemd utente (unit hermes-* installate da deploy/install_systemd.sh):
  start/stop via `systemctl --user`, riavvio automatico su crash incluso;
- legacy: subprocess + start.sh (già indurito con lo stale-kill), per gli
  ambienti senza systemd (es. WSL2 senza systemd=true in wsl.conf).

status() e tail_log() sono comuni: lo stato si legge dai processi vivi
(pgrep-like) e i log dai file loguru, chiunque abbia avviato il servizio.
"""
import os
import subprocess
import time
from pathlib import Path
from typing import List

import psutil

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PID_DIR = REPO_ROOT / "dashboard" / "pids"
LOGS_DIR = REPO_ROOT / "logs"

MODULES = {
    "engine": "src.engine.main",
    "inference": "src.inference.main",
    "sentiment": "src.sentiment.ollama_client",
}

UNITS = {
    "engine": "hermes-engine.service",
    "inference": "hermes-inference.service",
    "sentiment": "hermes-sentiment.service",
}

LOG_PREFIX = {
    "engine": "trading",
    "inference": "inference",
    "sentiment": "sentiment",
}


def systemd_ready(service: str = "engine") -> bool:
    """True se systemd è attivo E la unit utente del servizio è installata:
    solo allora start/stop passano da systemctl --user."""
    if not Path("/run/systemd/system").is_dir():
        return False
    unit = UNITS.get(service)
    if unit is None:
        return False
    return (Path.home() / ".config" / "systemd" / "user" / unit).exists()


def _systemctl(action: str, service: str) -> tuple[bool, str]:
    unit = UNITS[service]
    result = subprocess.run(
        ["systemctl", "--user", action, unit],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        verb = "Avviato" if action == "start" else "Fermato"
        return True, f"{verb} via systemd ({unit})"
    return False, f"systemctl {action} {unit} fallito: {result.stderr.strip() or result.stdout.strip()}"


def _pid_file(service: str) -> Path:
    return PID_DIR / f"{service}.pid"


def _find_running_pids(service: str) -> List[int]:
    module = MODULES[service]
    pids = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmdline = proc.info.get("cmdline") or []
        joined = " ".join(cmdline)
        if module in joined and "python" in joined:
            pids.append(proc.info["pid"])
    return pids


def start(service: str) -> tuple[bool, str]:
    if service not in MODULES:
        return False, f"Servizio sconosciuto: {service}"

    running = _find_running_pids(service)
    if running:
        return False, f"Già in esecuzione (PID {running})"

    if systemd_ready(service):
        return _systemctl("start", service)

    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = LOGS_DIR / f"{service}_stdout.log"

    with open(stdout_path, "a") as stdout_file:
        proc = subprocess.Popen(
            ["./start.sh", service],
            cwd=REPO_ROOT,
            stdout=stdout_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    _pid_file(service).write_text(str(proc.pid))
    return True, f"Avviato (PID {proc.pid})"


def stop(service: str, timeout: float = 8.0) -> tuple[bool, str]:
    if service not in MODULES:
        return False, f"Servizio sconosciuto: {service}"

    pids = _find_running_pids(service)
    if not pids:
        _pid_file(service).unlink(missing_ok=True)
        return False, "Nessun processo in esecuzione"

    if systemd_ready(service):
        # systemctl stop, non kill diretto: altrimenti Restart=always
        # farebbe ripartire subito il processo appena terminato
        return _systemctl("stop", service)

    for pid in pids:
        try:
            psutil.Process(pid).terminate()
        except psutil.NoSuchProcess:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(psutil.pid_exists(pid) for pid in pids):
            break
        time.sleep(0.2)

    still_alive = [pid for pid in pids if psutil.pid_exists(pid)]
    for pid in still_alive:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass

    _pid_file(service).unlink(missing_ok=True)
    return True, "Fermato" if not still_alive else "Fermato (forzato con SIGKILL)"


def status(service: str) -> dict:
    pids = _find_running_pids(service)
    return {"running": len(pids) > 0, "pids": pids}


def tail_log(service: str, n: int = 15) -> str:
    prefix = LOG_PREFIX.get(service, service)
    candidates = sorted(LOGS_DIR.glob(f"{prefix}_*.log"))
    if not candidates:
        return ""
    latest = candidates[-1]
    try:
        with open(latest, "r") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return ""
