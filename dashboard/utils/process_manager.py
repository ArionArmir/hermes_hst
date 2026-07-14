"""
Avvio/arresto/stato dei processi del bot (engine, inference, sentiment) via subprocess + PID file.
Riusa start.sh (già indurito con lo stale-kill) invece di duplicarne la logica.
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

LOG_PREFIX = {
    "engine": "trading",
    "inference": "inference",
    "sentiment": "sentiment",
}


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
