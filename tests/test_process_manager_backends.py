"""
Routing dei backend del process_manager: systemctl --user quando systemd è
attivo E le unit hermes-* sono installate, altrimenti il percorso legacy
subprocess+start.sh. Su questa macchina (WSL2 senza systemd) i test simulano
entrambi gli ambienti con monkeypatch.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

from utils import process_manager as pm


def test_systemd_not_ready_without_systemd_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pm.Path, "is_dir", lambda self: False)
    assert pm.systemd_ready("engine") is False


def test_systemd_not_ready_without_installed_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(pm.Path, "is_dir", lambda self: True)
    monkeypatch.setattr(pm.Path, "home", staticmethod(lambda: tmp_path))
    assert pm.systemd_ready("engine") is False


def test_systemd_ready_with_systemd_and_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(pm.Path, "is_dir", lambda self: True)
    monkeypatch.setattr(pm.Path, "home", staticmethod(lambda: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "hermes-engine.service").touch()

    assert pm.systemd_ready("engine") is True
    assert pm.systemd_ready("inference") is False  # unit non installata
    assert pm.systemd_ready("sconosciuto") is False


def test_start_routes_to_systemctl_when_ready():
    ok_result = MagicMock(returncode=0, stderr="", stdout="")
    with patch.object(pm, "systemd_ready", return_value=True), \
         patch.object(pm, "_find_running_pids", return_value=[]), \
         patch.object(pm.subprocess, "run", return_value=ok_result) as run:
        ok, msg = pm.start("engine")

    assert ok
    assert "systemd" in msg
    assert run.call_args[0][0] == ["systemctl", "--user", "start", "hermes-engine.service"]


def test_stop_routes_to_systemctl_when_ready():
    ok_result = MagicMock(returncode=0, stderr="", stdout="")
    with patch.object(pm, "systemd_ready", return_value=True), \
         patch.object(pm, "_find_running_pids", return_value=[123]), \
         patch.object(pm.subprocess, "run", return_value=ok_result) as run:
        ok, msg = pm.stop("engine")

    assert ok
    assert run.call_args[0][0] == ["systemctl", "--user", "stop", "hermes-engine.service"]


def test_systemctl_failure_is_reported():
    fail = MagicMock(returncode=1, stderr="Failed to connect to bus", stdout="")
    with patch.object(pm, "systemd_ready", return_value=True), \
         patch.object(pm, "_find_running_pids", return_value=[]), \
         patch.object(pm.subprocess, "run", return_value=fail):
        ok, msg = pm.start("engine")

    assert not ok
    assert "Failed to connect to bus" in msg


def test_legacy_path_used_without_systemd():
    with patch.object(pm, "systemd_ready", return_value=False), \
         patch.object(pm, "_find_running_pids", return_value=[]), \
         patch.object(pm.subprocess, "Popen") as popen:
        popen.return_value = MagicMock(pid=4242)
        ok, msg = pm.start("engine")

    assert ok
    assert "4242" in msg
    assert popen.call_args[0][0] == ["./start.sh", "engine"]
