"""System health monitor and log analyzer for LLM Wiki services.

Monitors:
- systemd user services (llm-wiki-router.service, llm-wiki-watcher.service)
- HTTP/SSE endpoint connectivity and response latency
- Knowledge vaults health, uningested backlogs, and quarantine folders
- Systemd journal logs for Python tracebacks, crashes, 429/503 rate limits
- Filesystem disk space
- Provider cooldowns and quota exhaustions

Reporting & Alerting:
- Sends alerts and recovery notices to the user's Discord home channel via Hermes gateway credentials
- Writes a rolling live Markdown report to MONITOR_REPORT.md
- Persists monitor state to ~/.config/llm-wiki/monitor_state.json for deduplication
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("llm-wiki.monitor")

DEFAULT_WIKIS_DIR = Path("/home/ubuntu/wikis")
DEFAULT_SERVICES = ["llm-wiki-router.service", "llm-wiki-watcher.service"]
DEFAULT_STATE_FILE = Path.home() / ".config" / "llm-wiki" / "monitor_state.json"
DEFAULT_REPORT_FILE = DEFAULT_WIKIS_DIR / "MONITOR_REPORT.md"
HERMES_ENV_FILE = Path.home() / ".hermes" / ".env"

DISCORD_API = "https://discord.com/api/v10"
DISCORD_USER_AGENT = "DiscordBot (https://github.com/gowtham, llm-wiki-monitor)"

# Embed Colors
COLOR_CRITICAL = 0xE74C3C  # Red
COLOR_WARNING = 0xE67E22   # Amber/Orange
COLOR_RECOVERY = 0x2ECC71  # Green
COLOR_INFO = 0x3498DB      # Blue


def load_discord_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Extract Discord bot token and home channel ID from ~/.hermes/.env or os.environ."""
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_HOME_CHANNEL")

    if (not bot_token or not channel_id) and HERMES_ENV_FILE.exists():
        try:
            for line in HERMES_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "DISCORD_BOT_TOKEN" and not bot_token:
                    bot_token = v
                elif k == "DISCORD_HOME_CHANNEL" and not channel_id:
                    channel_id = v
        except Exception as e:
            logger.warning("Failed reading credentials from %s: %s", HERMES_ENV_FILE, e)

    return bot_token, channel_id


def send_discord_alert(
    title: str,
    description: str,
    fields: Optional[List[Dict[str, Any]]] = None,
    severity: str = "critical",
    bot_token: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> bool:
    """Send a structured alert embed to the user's Discord home channel."""
    token, ch_id = bot_token, channel_id
    if not token or not ch_id:
        token, ch_id = load_discord_credentials()

    if not token or not ch_id:
        logger.warning("Cannot send Discord alert: DISCORD_BOT_TOKEN or DISCORD_HOME_CHANNEL missing")
        return False

    color_map = {
        "critical": COLOR_CRITICAL,
        "warning": COLOR_WARNING,
        "recovery": COLOR_RECOVERY,
        "info": COLOR_INFO,
    }
    color = color_map.get(severity.lower(), COLOR_INFO)

    embed_payload: Dict[str, Any] = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "footer": {"text": "LLM Wiki System Monitor"},
    }
    if fields:
        embed_payload["fields"] = fields[:25]

    payload = {
        "content": "" if severity != "critical" else "⚠️ **LLM Wiki Server Alert**",
        "embeds": [embed_payload],
    }

    url = f"{DISCORD_API}/channels/{ch_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent": DISCORD_USER_AGENT,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        logger.error("Failed sending Discord alert: %s", e)
        return False


def check_systemd_services(services: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Query systemd user units for active state, PID, memory, and CPU usage."""
    if services is None:
        services = DEFAULT_SERVICES

    results = []
    for svc in services:
        info: Dict[str, Any] = {
            "name": svc,
            "active": False,
            "substate": "unknown",
            "pid": 0,
            "status": "down",
            "memory_mb": 0.0,
            "uptime": "N/A",
            "exit_code": None,
        }
        try:
            cmd = [
                "systemctl",
                "--user",
                "show",
                svc,
                "--property=ActiveState,SubState,MainPID,ExecMainStatus,ActiveEnterTimestamp",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            props = {}
            for line in res.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()

            active_state = props.get("ActiveState", "unknown")
            sub_state = props.get("SubState", "unknown")
            pid = int(props.get("MainPID", "0") or "0")
            exit_code = props.get("ExecMainStatus")

            info["active"] = (active_state == "active" and sub_state == "running")
            info["substate"] = sub_state
            info["pid"] = pid
            info["exit_code"] = int(exit_code) if exit_code and exit_code.isdigit() else None
            info["uptime"] = props.get("ActiveEnterTimestamp", "N/A")

            if info["active"]:
                info["status"] = "running"
                if pid > 0:
                    try:
                        ps_res = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "rss="],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        rss_kb = int(ps_res.stdout.strip() or "0")
                        info["memory_mb"] = round(rss_kb / 1024.0, 1)
                    except Exception:
                        pass
            elif active_state == "failed":
                info["status"] = "failed"
            else:
                info["status"] = "stopped"

        except Exception as e:
            info["status"] = "error"
            info["error"] = str(e)

        results.append(info)
    return results


def check_http_endpoint(
    host: str = "100.99.243.66",
    port: int = 8765,
    path: str = "/sse",
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Test HTTP connectivity and latency of the MCP router endpoint."""
    url = f"http://{host}:{port}{path}"
    start_time = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LLMWikiMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = (time.perf_counter() - start_time) * 1000.0
            return {
                "reachable": True,
                "status_code": resp.status,
                "latency_ms": round(latency, 2),
                "url": url,
                "error": None,
            }
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - start_time) * 1000.0
        # If SSE endpoint returns 200 or 405 or 404, it is listening and reachable
        return {
            "reachable": True,
            "status_code": e.code,
            "latency_ms": round(latency, 2),
            "url": url,
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        latency = (time.perf_counter() - start_time) * 1000.0
        return {
            "reachable": False,
            "status_code": 0,
            "latency_ms": round(latency, 2),
            "url": url,
            "error": str(e),
        }


def check_vaults_and_storage(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Inspect watched vaults for page counts, backlogs, and quarantine files."""
    if base_dir is None:
        base_dir = DEFAULT_WIKIS_DIR

    vault_stats: List[Dict[str, Any]] = []
    quarantined_files: List[Dict[str, str]] = []
    backlog_files: List[Dict[str, str]] = []

    if base_dir.exists():
        for item in sorted(base_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("."):
                continue

            raw_dir = item / "raw"
            wiki_dir = item / "wiki"
            cache_dir = wiki_dir / ".llm-wiki" / "raw_cache"

            v_info: Dict[str, Any] = {
                "name": item.name,
                "path": str(item),
                "is_vault": (raw_dir.exists() or wiki_dir.exists()),
                "total_pages": 0,
                "backlog_count": 0,
                "quarantine_count": 0,
            }

            if wiki_dir.exists():
                v_info["total_pages"] = len([
                    p for p in wiki_dir.rglob("*.md")
                    if not p.name.startswith(".") and ".llm-wiki" not in p.parts
                ])

            # Check quarantine folders
            for q_name in ("failed", "quarantine"):
                q_dir = raw_dir / q_name
                if q_dir.exists():
                    for qf in q_dir.glob("*"):
                        if qf.is_file() and not qf.name.startswith("."):
                            v_info["quarantine_count"] += 1
                            quarantined_files.append({
                                "vault": item.name,
                                "path": str(qf),
                                "filename": qf.name,
                            })

            # Check uncompiled backlog in raw/
            if raw_dir.exists():
                for rf in raw_dir.glob("*"):
                    if rf.is_file() and not rf.name.startswith(".") and rf.suffix.lower() in (".md", ".txt", ".pdf"):
                        cached = cache_dir / rf.name if cache_dir.exists() else None
                        if not cached or not cached.exists():
                            v_info["backlog_count"] += 1
                            backlog_files.append({
                                "vault": item.name,
                                "path": str(rf),
                                "filename": rf.name,
                            })

            vault_stats.append(v_info)

    # Disk usage
    disk_info = {"total_gb": 0.0, "free_gb": 0.0, "used_percent": 0.0}
    try:
        check_path = base_dir if base_dir.exists() else Path.home()
        usage = shutil.disk_usage(check_path)
        disk_info = {
            "total_gb": round(usage.total / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "used_percent": round((usage.used / usage.total) * 100.0, 1),
        }
    except Exception as e:
        logger.warning("Failed checking disk usage: %s", e)

    return {
        "base_dir": str(base_dir),
        "vaults": vault_stats,
        "quarantined_files": quarantined_files,
        "backlog_files": backlog_files,
        "disk": disk_info,
    }


def check_journal_logs(
    services: Optional[List[str]] = None,
    since_seconds: int = 300,
) -> Dict[str, Any]:
    """Scan systemd journal for recent errors, tracebacks, and rate-limits."""
    if services is None:
        services = DEFAULT_SERVICES

    errors_found: List[Dict[str, Any]] = []
    warnings_found: List[Dict[str, Any]] = []

    error_patterns = [
        re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
        re.compile(r"RuntimeError:.*", re.IGNORECASE),
        re.compile(r"KeyError:.*", re.IGNORECASE),
        re.compile(r"ValueError:.*", re.IGNORECASE),
        re.compile(r"TypeError:.*", re.IGNORECASE),
        re.compile(r"Ingestion error for .*", re.IGNORECASE),
        re.compile(r"500 Internal Server Error", re.IGNORECASE),
        re.compile(r"Failed with result 'exit-code'", re.IGNORECASE),
    ]

    warning_patterns = [
        re.compile(r"429.*quota", re.IGNORECASE),
        re.compile(r"503.*high demand", re.IGNORECASE),
        re.compile(r"cooling down", re.IGNORECASE),
        re.compile(r"Daily quota exhausted", re.IGNORECASE),
        re.compile(r"RateLimitCooldownTracker", re.IGNORECASE),
        re.compile(r"quarantined", re.IGNORECASE),
    ]

    for svc in services:
        try:
            cmd = [
                "journalctl",
                "--user",
                "-u",
                svc,
                f"--since=-{since_seconds}s",
                "--no-pager",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            lines = res.stdout.splitlines()

            i = 0
            while i < len(lines):
                line = lines[i]

                # Traceback capture (multi-line)
                if "Traceback (most recent call last):" in line:
                    tb_chunk = [line]
                    j = i + 1
                    while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t") or ":" in lines[j]):
                        tb_chunk.append(lines[j])
                        if not lines[j].startswith(" ") and not lines[j].startswith("\t") and len(tb_chunk) > 2:
                            break
                        j += 1
                    error_snippet = "\n".join(tb_chunk[:15])
                    errors_found.append({
                        "service": svc,
                        "type": "Traceback",
                        "summary": tb_chunk[-1].strip() if tb_chunk else "Python Traceback",
                        "snippet": error_snippet,
                    })
                    i = max(i + 1, j)
                    continue

                for pat in error_patterns:
                    if pat.search(line):
                        errors_found.append({
                            "service": svc,
                            "type": "Error",
                            "summary": line.strip()[:200],
                            "snippet": line.strip(),
                        })
                        break

                for w_pat in warning_patterns:
                    if w_pat.search(line):
                        warnings_found.append({
                            "service": svc,
                            "type": "Warning",
                            "summary": line.strip()[:200],
                            "snippet": line.strip(),
                        })
                        break

                i += 1

        except Exception as e:
            logger.warning("Failed querying journalctl for %s: %s", svc, e)

    return {
        "since_seconds": since_seconds,
        "errors": errors_found,
        "warnings": warnings_found,
    }


def check_provider_cooldowns() -> Dict[str, Any]:
    """Retrieve active LLM provider rate-limit cooldowns."""
    try:
        from lib.providers import RateLimitCooldownTracker
        cooldowns = RateLimitCooldownTracker.get_cooldowns()
        return {
            "active_cooldowns": cooldowns,
            "has_exhaustions": any(dur >= 300 for dur in cooldowns.values()),
        }
    except Exception as e:
        return {"active_cooldowns": {}, "error": str(e), "has_exhaustions": False}


class MonitorRunner:
    """Manages monitoring cycles, state persistence, alert dispatching, and reporting."""

    def __init__(
        self,
        wikis_dir: Path = DEFAULT_WIKIS_DIR,
        state_file: Path = DEFAULT_STATE_FILE,
        report_file: Path = DEFAULT_REPORT_FILE,
    ):
        self.wikis_dir = wikis_dir
        self.state_file = state_file
        self.report_file = report_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "last_check": None,
            "services": {},
            "active_incidents": {},
            "history": [],
        }

    def save_state(self, state: Dict[str, Any]):
        try:
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("Failed saving monitor state: %s", e)

    def run_check(self, since_seconds: int = 300, send_alerts: bool = True) -> Dict[str, Any]:
        """Execute one full diagnostic evaluation cycle."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

        state = self.load_state()
        prev_services = state.get("services", {})
        active_incidents = state.get("active_incidents", {})

        # 1. Gather all subsystem telemetry
        services_data = check_systemd_services()
        http_data = check_http_endpoint()
        vaults_data = check_vaults_and_storage(self.wikis_dir)
        logs_data = check_journal_logs(since_seconds=since_seconds)
        cooldowns_data = check_provider_cooldowns()

        new_incidents: List[Dict[str, Any]] = []
        recoveries: List[Dict[str, Any]] = []

        # 2. Evaluate service states
        current_services = {}
        for s in services_data:
            s_name = s["name"]
            current_services[s_name] = s["status"]
            prev_status = prev_services.get(s_name, "running")

            if s["status"] != "running":
                inc_id = f"service_down_{s_name}"
                inc = {
                    "id": inc_id,
                    "type": "service_down",
                    "severity": "critical",
                    "service": s_name,
                    "title": f"Service Down: {s_name}",
                    "description": f"Service `{s_name}` is currently in state `{s['status']}` (substate: `{s['substate']}`, exit-code: {s.get('exit_code')}).",
                    "timestamp": now_str,
                }
                if inc_id not in active_incidents:
                    new_incidents.append(inc)
                active_incidents[inc_id] = inc
            elif prev_status != "running" and s["status"] == "running":
                # Recovered
                inc_id = f"service_down_{s_name}"
                if inc_id in active_incidents:
                    active_incidents.pop(inc_id, None)
                    recoveries.append({
                        "id": inc_id,
                        "title": f"Service Restored: {s_name}",
                        "description": f"Service `{s_name}` has recovered and is now active and running (PID {s['pid']}, Memory {s['memory_mb']} MB).",
                        "timestamp": now_str,
                    })

        # 3. Evaluate HTTP Router reachability
        if not http_data["reachable"]:
            inc_id = "http_router_unreachable"
            inc = {
                "id": inc_id,
                "type": "http_unreachable",
                "severity": "critical",
                "service": "llm-wiki-router.service",
                "title": "MCP Router HTTP Endpoint Unreachable",
                "description": f"Failed reaching `{http_data['url']}`: {http_data['error']}",
                "timestamp": now_str,
            }
            if inc_id not in active_incidents:
                new_incidents.append(inc)
            active_incidents[inc_id] = inc
        else:
            if "http_router_unreachable" in active_incidents:
                active_incidents.pop("http_router_unreachable", None)
                recoveries.append({
                    "id": "http_router_unreachable",
                    "title": "MCP Router HTTP Restored",
                    "description": f"Router endpoint `{http_data['url']}` is responding normally ({http_data['latency_ms']} ms).",
                    "timestamp": now_str,
                })

        # 4. Evaluate Quarantined Files
        if vaults_data["quarantined_files"]:
            inc_id = "files_quarantined"
            files_list = ", ".join(f"`{f['filename']}` ({f['vault']})" for f in vaults_data["quarantined_files"][:5])
            inc = {
                "id": inc_id,
                "type": "quarantine",
                "severity": "critical",
                "service": "llm-wiki-watcher.service",
                "title": f"Files Quarantined: {len(vaults_data['quarantined_files'])} File(s)",
                "description": f"Watcher quarantined repeatedly-failing file(s) to `raw/failed/`: {files_list}.",
                "timestamp": now_str,
            }
            if inc_id not in active_incidents:
                new_incidents.append(inc)
            active_incidents[inc_id] = inc
        else:
            if "files_quarantined" in active_incidents:
                active_incidents.pop("files_quarantined", None)
                recoveries.append({
                    "id": "files_quarantined",
                    "title": "Quarantine Resolved",
                    "description": "All quarantined files in `raw/failed/` have been processed or cleared.",
                    "timestamp": now_str,
                })

        # 5. Evaluate Log Errors & Tracebacks
        for err in logs_data["errors"]:
            err_hash = f"err_{abs(hash(err['summary'])) % 10000000}"
            inc = {
                "id": err_hash,
                "type": "log_error",
                "severity": "critical",
                "service": err["service"],
                "title": f"Server Crash/Error in {err['service']}",
                "description": f"**{err['summary']}**\n```\n{err['snippet'][:1500]}\n```",
                "timestamp": now_str,
            }
            # Only alert on new error hashes
            if err_hash not in active_incidents:
                new_incidents.append(inc)
                active_incidents[err_hash] = inc

        # 6. Evaluate Disk Usage (>90%)
        if vaults_data["disk"]["used_percent"] >= 90.0:
            inc_id = "disk_full"
            inc = {
                "id": inc_id,
                "type": "disk_space",
                "severity": "warning",
                "service": "filesystem",
                "title": f"Low Disk Space: {vaults_data['disk']['used_percent']}% Used",
                "description": f"Disk partition has only {vaults_data['disk']['free_gb']} GB free out of {vaults_data['disk']['total_gb']} GB.",
                "timestamp": now_str,
            }
            if inc_id not in active_incidents:
                new_incidents.append(inc)
            active_incidents[inc_id] = inc

        # Dispatch Discord Alerts
        if send_alerts:
            for inc in new_incidents:
                send_discord_alert(
                    title=f"🚨 {inc['title']}",
                    description=inc["description"],
                    fields=[
                        {"name": "Service", "value": inc.get("service", "N/A"), "inline": True},
                        {"name": "Severity", "value": inc.get("severity", "critical").upper(), "inline": True},
                        {"name": "Timestamp", "value": inc["timestamp"], "inline": True},
                    ],
                    severity=inc.get("severity", "critical"),
                )

            for rec in recoveries:
                send_discord_alert(
                    title=f"✅ {rec['title']}",
                    description=rec["description"],
                    fields=[
                        {"name": "Status", "value": "RESOLVED / OPERATIONAL", "inline": True},
                        {"name": "Timestamp", "value": rec["timestamp"], "inline": True},
                    ],
                    severity="recovery",
                )

        # 7. Update State
        state["last_check"] = now_str
        state["services"] = current_services
        state["active_incidents"] = active_incidents

        # Keep rolling history of last 50 events
        history = state.get("history", [])
        for inc in new_incidents:
            history.insert(0, {"event": "INCIDENT", **inc})
        for rec in recoveries:
            history.insert(0, {"event": "RECOVERY", **rec})
        state["history"] = history[:50]

        self.save_state(state)

        # 8. Generate and save Markdown report
        report_content = self.generate_markdown_report(
            services_data=services_data,
            http_data=http_data,
            vaults_data=vaults_data,
            logs_data=logs_data,
            cooldowns_data=cooldowns_data,
            active_incidents=list(active_incidents.values()),
            recent_history=history[:10],
            timestamp=now_str,
        )
        try:
            self.report_file.parent.mkdir(parents=True, exist_ok=True)
            self.report_file.write_text(report_content, encoding="utf-8")
        except Exception as e:
            logger.error("Failed writing markdown report to %s: %s", self.report_file, e)

        return {
            "timestamp": now_str,
            "healthy": (len(active_incidents) == 0),
            "services": services_data,
            "http": http_data,
            "vaults": vaults_data,
            "active_incidents_count": len(active_incidents),
            "new_incidents": new_incidents,
            "recoveries": recoveries,
        }

    def generate_markdown_report(
        self,
        services_data: List[Dict[str, Any]],
        http_data: Dict[str, Any],
        vaults_data: Dict[str, Any],
        logs_data: Dict[str, Any],
        cooldowns_data: Dict[str, Any],
        active_incidents: List[Dict[str, Any]],
        recent_history: List[Dict[str, Any]],
        timestamp: str,
    ) -> str:
        """Render a clean, GitHub-Flavored Markdown status report."""
        status_banner = "🟢 **All Systems Operational**" if not active_incidents else f"🔴 **{len(active_incidents)} Active Incident(s) Detected**"

        md = [
            "# LLM Wiki Server — System Health Report",
            "",
            f"> Last Monitored: **{timestamp}** · Check Frequency: **Every 5 minutes**",
            "",
            f"### Status: {status_banner}",
            "",
            "---",
            "",
            "## 1. System Services",
            "",
            "| Service | Status | Substate | PID | Memory (RSS) | Uptime |",
            "|---|---|---|---|---|---|",
        ]

        for s in services_data:
            icon = "🟢" if s["status"] == "running" else "🔴"
            mem = f"{s['memory_mb']} MB" if s["memory_mb"] > 0 else "0 MB"
            md.append(f"| `{s['name']}` | {icon} **{s['status'].upper()}** | `{s['substate']}` | `{s['pid']}` | {mem} | {s['uptime']} |")

        md.extend([
            "",
            "## 2. Network & Router Connectivity",
            "",
            f"- **Endpoint:** `{http_data['url']}`",
            f"- **Reachable:** {'✅ Yes' if http_data['reachable'] else '❌ No'}",
            f"- **HTTP Status:** `{http_data['status_code']}`",
            f"- **Latency:** `{http_data['latency_ms']} ms`",
            f"- **Error:** `{http_data['error'] or 'None'}`",
            "",
            "## 3. Watched Vaults & Storage",
            "",
            f"- **Disk Space:** `{vaults_data['disk']['free_gb']} GB free` / `{vaults_data['disk']['total_gb']} GB total` (**{vaults_data['disk']['used_percent']}% used**)",
            f"- **Quarantined Files:** `{len(vaults_data['quarantined_files'])}`",
            f"- **Pending Backlog:** `{len(vaults_data['backlog_files'])}`",
            "",
            "| Vault Name | Total Compiled Pages | Pending Backlog | Quarantined Files |",
            "|---|---|---|---|",
        ])

        for v in vaults_data["vaults"]:
            q_str = f"⚠️ **{v['quarantine_count']}**" if v['quarantine_count'] > 0 else "0"
            b_str = f"**{v['backlog_count']}**" if v['backlog_count'] > 0 else "0"
            md.append(f"| **{v['name']}** | {v['total_pages']} | {b_str} | {q_str} |")

        if active_incidents:
            md.extend([
                "",
                "## 4. 🚨 Active Incidents",
                "",
            ])
            for inc in active_incidents:
                md.append(f"### ⚠️ {inc['title']}")
                md.append(f"- **Service:** `{inc.get('service', 'N/A')}`")
                md.append(f"- **Detected At:** {inc['timestamp']}")
                md.append(f"- **Details:**\n\n{inc['description']}\n")
        else:
            md.extend([
                "",
                "## 4. Active Incidents",
                "",
                "No active incidents or crashes detected. Services and watchers running normally.",
            ])

        # Provider Cooldowns
        cooldowns = cooldowns_data.get("active_cooldowns", {})
        md.extend([
            "",
            "## 5. LLM Provider Cooldowns",
            "",
        ])
        if cooldowns:
            md.append("| Model / Provider | Remaining Cooldown |")
            md.append("|---|---|")
            for m, dur in cooldowns.items():
                md.append(f"| `{m}` | {dur}s |")
        else:
            md.append("All configured LLM providers and models are healthy (0 active cooldowns).")

        # Recent Incident & Recovery History
        if recent_history:
            md.extend([
                "",
                "## 6. Recent Audit History",
                "",
                "| Timestamp | Event | Summary |",
                "|---|---|---|",
            ])
            for h in recent_history:
                icon = "🚨" if h.get("event") == "INCIDENT" else "✅"
                md.append(f"| {h.get('timestamp', 'N/A')} | {icon} {h.get('event')} | {h.get('title')} |")

        return "\n".join(md) + "\n"
