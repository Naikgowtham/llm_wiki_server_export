"""Unit and integration tests for LLM Wiki System Monitor and Diagnostics."""

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.monitor_cmd import monitor_cmd
from lib.monitor import (
    COLOR_CRITICAL,
    COLOR_RECOVERY,
    COLOR_WARNING,
    MonitorRunner,
    check_http_endpoint,
    check_journal_logs,
    check_systemd_services,
    check_vaults_and_storage,
    load_discord_credentials,
    send_discord_alert,
)


def test_load_discord_credentials(tmp_path: Path, monkeypatch):
    """Test credential discovery from environment and hermes .env file."""
    # 1. From environment
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token-123")
    monkeypatch.setenv("DISCORD_HOME_CHANNEL", "env-channel-456")
    tok, ch = load_discord_credentials()
    assert tok == "env-token-123"
    assert ch == "env-channel-456"

    # 2. From .env file fallback
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_HOME_CHANNEL", raising=False)

    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "# Hermes config\n"
        "DISCORD_BOT_TOKEN=file-token-999\n"
        "DISCORD_HOME_CHANNEL=\"987654321\"\n"
    )
    monkeypatch.setattr("lib.monitor.HERMES_ENV_FILE", fake_env)

    tok2, ch2 = load_discord_credentials()
    assert tok2 == "file-token-999"
    assert ch2 == "987654321"


def test_send_discord_alert_payload_formatting():
    """Test Discord alert JSON payload construction and color mapping."""
    captured_payloads = []

    class DummyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=12):
        data = json.loads(req.data.decode("utf-8"))
        captured_payloads.append((req, data))
        return DummyResponse()

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # Critical alert
        ok = send_discord_alert(
            title="Service Crashed",
            description="router exited with code 1",
            severity="critical",
            bot_token="test-token",
            channel_id="12345",
        )
        assert ok is True
        assert len(captured_payloads) == 1
        req, payload = captured_payloads[0]
        assert req.headers["Authorization"] == "Bot test-token"
        assert payload["embeds"][0]["title"] == "Service Crashed"
        assert payload["embeds"][0]["color"] == COLOR_CRITICAL

        # Recovery alert
        ok2 = send_discord_alert(
            title="Service Recovered",
            description="router is back online",
            severity="recovery",
            bot_token="test-token",
            channel_id="12345",
        )
        assert ok2 is True
        _, payload2 = captured_payloads[1]
        assert payload2["embeds"][0]["color"] == COLOR_RECOVERY


def test_check_systemd_services():
    """Test parsing systemctl show output for running and failed services."""
    mock_running = (
        "ActiveState=active\n"
        "SubState=running\n"
        "MainPID=12345\n"
        "ExecMainStatus=0\n"
        "ActiveEnterTimestamp=Fri 2026-10-09 10:00:00 UTC\n"
    )
    mock_failed = (
        "ActiveState=failed\n"
        "SubState=failed\n"
        "MainPID=0\n"
        "ExecMainStatus=1\n"
        "ActiveEnterTimestamp=Fri 2026-10-09 10:00:00 UTC\n"
    )

    def mock_run(cmd, **kwargs):
        svc_name = cmd[3]
        stdout = mock_running if "router" in svc_name else mock_failed
        return MagicMock(stdout=stdout, returncode=0)

    with patch("subprocess.run", side_effect=mock_run):
        res = check_systemd_services(["llm-wiki-router.service", "llm-wiki-watcher.service"])
        assert len(res) == 2
        router = res[0]
        watcher = res[1]

        assert router["name"] == "llm-wiki-router.service"
        assert router["status"] == "running"
        assert router["active"] is True
        assert router["pid"] == 12345

        assert watcher["name"] == "llm-wiki-watcher.service"
        assert watcher["status"] == "failed"
        assert watcher["active"] is False
        assert watcher["exit_code"] == 1


def test_check_http_endpoint():
    """Test checking HTTP router endpoint for success and error scenarios."""
    class DummyResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    # Success case
    with patch("urllib.request.urlopen", return_value=DummyResp()):
        res = check_http_endpoint(host="127.0.0.1", port=8765)
        assert res["reachable"] is True
        assert res["status_code"] == 200
        assert res["latency_ms"] >= 0.0

    # Failure case
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        res_fail = check_http_endpoint(host="127.0.0.1", port=8765)
        assert res_fail["reachable"] is False
        assert "Connection refused" in res_fail["error"]


def test_check_vaults_and_storage(tmp_path: Path):
    """Test inspecting vault counts, backlogs, and quarantine files."""
    vault1 = tmp_path / "wiki-a"
    (vault1 / "wiki" / "concepts").mkdir(parents=True)
    (vault1 / "raw" / "failed").mkdir(parents=True)
    (vault1 / "wiki" / ".llm-wiki" / "raw_cache").mkdir(parents=True)

    # 2 compiled notes
    (vault1 / "wiki" / "concepts" / "note1.md").write_text("# Note 1", encoding="utf-8")
    (vault1 / "wiki" / "concepts" / "note2.md").write_text("# Note 2", encoding="utf-8")

    # 1 quarantined file
    (vault1 / "raw" / "failed" / "bad.pdf").write_text("corrupted", encoding="utf-8")

    # 1 raw backlog file (not in cache)
    (vault1 / "raw" / "new.md").write_text("# New Raw", encoding="utf-8")

    data = check_vaults_and_storage(base_dir=tmp_path)
    assert len(data["vaults"]) == 1
    v = data["vaults"][0]
    assert v["name"] == "wiki-a"
    assert v["total_pages"] == 2
    assert v["quarantine_count"] == 1
    assert v["backlog_count"] == 1

    assert len(data["quarantined_files"]) == 1
    assert data["quarantined_files"][0]["filename"] == "bad.pdf"

    assert len(data["backlog_files"]) == 1
    assert data["backlog_files"][0]["filename"] == "new.md"


def test_check_journal_logs_traceback_and_warnings():
    """Test parsing multi-line Python Traceback and warning patterns from journalctl."""
    mock_journal = (
        "Oct 09 12:00:00 app-server wiki[100]: Starting Watcher...\n"
        "Oct 09 12:00:01 app-server wiki[100]: Traceback (most recent call last):\n"
        "Oct 09 12:00:01 app-server wiki[100]:   File 'wiki_ops.py', line 50, in run\n"
        "Oct 09 12:00:01 app-server wiki[100]: KeyError: 'domain_name'\n"
        "Oct 09 12:00:02 app-server wiki[100]: 429 Daily quota exhausted for model\n"
        "Oct 09 12:00:03 app-server wiki[100]: Normal log line\n"
    )

    with patch("subprocess.run", return_value=MagicMock(stdout=mock_journal)):
        res = check_journal_logs(services=["llm-wiki-watcher.service"], since_seconds=300)
        assert len(res["errors"]) >= 1
        tb = [e for e in res["errors"] if e["type"] == "Traceback"][0]
        assert "KeyError: 'domain_name'" in tb["snippet"]

        assert len(res["warnings"]) >= 1
        warn = res["warnings"][0]
        assert "429 Daily quota exhausted" in warn["summary"]


def test_monitor_runner_lifecycle(tmp_path: Path):
    """Test full MonitorRunner cycle: incident detection, deduplication, recovery, and report."""
    state_file = tmp_path / "state.json"
    report_file = tmp_path / "MONITOR_REPORT.md"
    wikis_dir = tmp_path / "wikis"
    wikis_dir.mkdir()

    runner = MonitorRunner(wikis_dir=wikis_dir, state_file=state_file, report_file=report_file)

    # Cycle 1: All healthy
    with patch("lib.monitor.check_systemd_services", return_value=[
        {"name": "llm-wiki-router.service", "status": "running", "active": True, "substate": "running", "pid": 11, "memory_mb": 50.0, "uptime": "now"}
    ]), patch("lib.monitor.check_http_endpoint", return_value={
        "reachable": True, "status_code": 200, "latency_ms": 10.0, "url": "http://test", "error": None
    }), patch("lib.monitor.check_journal_logs", return_value={"errors": [], "warnings": []}), \
       patch("lib.monitor.send_discord_alert") as mock_alert:

        res1 = runner.run_check(send_alerts=True)
        assert res1["healthy"] is True
        assert res1["active_incidents_count"] == 0
        assert mock_alert.call_count == 0
        assert report_file.exists()
        assert "🟢 **All Systems Operational**" in report_file.read_text(encoding="utf-8")

    # Cycle 2: Service crashes -> alerts dispatched
    with patch("lib.monitor.check_systemd_services", return_value=[
        {"name": "llm-wiki-router.service", "status": "failed", "active": False, "substate": "failed", "pid": 0, "memory_mb": 0.0, "exit_code": 1, "uptime": "now"}
    ]), patch("lib.monitor.check_http_endpoint", return_value={
        "reachable": False, "status_code": 0, "latency_ms": 0.0, "url": "http://test", "error": "Connection Refused"
    }), patch("lib.monitor.check_journal_logs", return_value={"errors": [], "warnings": []}), \
       patch("lib.monitor.send_discord_alert") as mock_alert2:

        res2 = runner.run_check(send_alerts=True)
        assert res2["healthy"] is False
        assert res2["active_incidents_count"] == 2  # service down + http unreachable
        assert len(res2["new_incidents"]) == 2
        assert mock_alert2.call_count == 2
        assert "🔴" in report_file.read_text(encoding="utf-8")

    # Cycle 3: Deduplication -> no repeat alerts while still failing
    with patch("lib.monitor.check_systemd_services", return_value=[
        {"name": "llm-wiki-router.service", "status": "failed", "active": False, "substate": "failed", "pid": 0, "memory_mb": 0.0, "exit_code": 1, "uptime": "now"}
    ]), patch("lib.monitor.check_http_endpoint", return_value={
        "reachable": False, "status_code": 0, "latency_ms": 0.0, "url": "http://test", "error": "Connection Refused"
    }), patch("lib.monitor.check_journal_logs", return_value={"errors": [], "warnings": []}), \
       patch("lib.monitor.send_discord_alert") as mock_alert3:

        res3 = runner.run_check(send_alerts=True)
        assert len(res3["new_incidents"]) == 0  # Deduplicated!
        assert mock_alert3.call_count == 0

    # Cycle 4: Recovery -> recovery alert sent
    with patch("lib.monitor.check_systemd_services", return_value=[
        {"name": "llm-wiki-router.service", "status": "running", "active": True, "substate": "running", "pid": 12, "memory_mb": 55.0, "uptime": "now"}
    ]), patch("lib.monitor.check_http_endpoint", return_value={
        "reachable": True, "status_code": 200, "latency_ms": 12.0, "url": "http://test", "error": None
    }), patch("lib.monitor.check_journal_logs", return_value={"errors": [], "warnings": []}), \
       patch("lib.monitor.send_discord_alert") as mock_alert4:

        res4 = runner.run_check(send_alerts=True)
        assert res4["healthy"] is True
        assert len(res4["recoveries"]) == 2
        assert mock_alert4.call_count == 2


def test_cli_monitor_commands(tmp_path: Path):
    """Test Click CLI commands for wiki monitor."""
    runner = CliRunner()

    with patch("lib.monitor.check_systemd_services", return_value=[]), \
         patch("lib.monitor.check_http_endpoint", return_value={"reachable": True, "status_code": 200, "latency_ms": 1.0, "url": "http://u", "error": None}), \
         patch("lib.monitor.check_vaults_and_storage", return_value={"vaults": [], "disk": {"free_gb": 10, "total_gb": 20, "used_percent": 50}, "quarantined_files": [], "backlog_files": []}), \
         patch("lib.monitor.check_journal_logs", return_value={"errors": [], "warnings": []}), \
         patch("lib.monitor.check_provider_cooldowns", return_value={"active_cooldowns": {}}):

        res_status = runner.invoke(monitor_cmd, ["status"])
        assert res_status.exit_code == 0
        assert "LLM Wiki Server — Real-Time Health Diagnostics" in res_status.output

        report_p = tmp_path / "test_report.md"
        report_p.write_text("# Test Report Content", encoding="utf-8")
        res_report = runner.invoke(monitor_cmd, ["report", "-r", str(report_p)])
        assert res_report.exit_code == 0
        assert "Test Report Content" in res_report.output
