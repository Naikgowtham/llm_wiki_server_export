"""CLI command for LLM Wiki System Monitor and Health Diagnostics."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lib.monitor import (
    DEFAULT_REPORT_FILE,
    DEFAULT_WIKIS_DIR,
    MonitorRunner,
    check_http_endpoint,
    check_journal_logs,
    check_provider_cooldowns,
    check_systemd_services,
    check_vaults_and_storage,
    send_discord_alert,
)

console = Console()


@click.group("monitor", invoke_without_command=True)
@click.pass_context
def monitor_cmd(ctx: click.Context):
    """Monitor LLM Wiki services, inspect logs, detect bugs, and dispatch alerts."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_cmd)


@monitor_cmd.command("status")
@click.option("--base-dir", "-b", default=str(DEFAULT_WIKIS_DIR), type=click.Path(), help="Base directory containing wiki vaults.")
def status_cmd(base_dir: str):
    """Display real-time health scorecard for services, HTTP router, vaults, and logs."""
    wikis_dir = Path(base_dir)

    console.print(Panel.fit("[bold cyan]LLM Wiki Server — Real-Time Health Diagnostics[/bold cyan]", border_style="cyan"))

    # 1. System Services
    services = check_systemd_services()
    svc_table = Table(title="System Services", show_header=True, header_style="bold magenta")
    svc_table.add_column("Unit Name", style="bold")
    svc_table.add_column("Status", justify="center")
    svc_table.add_column("PID", justify="right")
    svc_table.add_column("Memory (RSS)", justify="right")
    svc_table.add_column("Uptime / Started", style="dim")

    for s in services:
        status_style = "[green]RUNNING[/green]" if s["status"] == "running" else f"[red]{s['status'].upper()}[/red]"
        mem = f"{s['memory_mb']} MB" if s["memory_mb"] > 0 else "-"
        pid_str = str(s["pid"]) if s["pid"] > 0 else "-"
        svc_table.add_row(s["name"], status_style, pid_str, mem, s["uptime"])
    console.print(svc_table)

    # 2. HTTP Router Check
    http_data = check_http_endpoint()
    http_status = "[green]ONLINE[/green]" if http_data["reachable"] else "[red]OFFLINE[/red]"
    console.print(
        f"\n[bold]MCP Router Endpoint:[/bold] {http_status} · "
        f"[cyan]{http_data['url']}[/cyan] · "
        f"Latency: [bold yellow]{http_data['latency_ms']} ms[/bold yellow] "
        f"(HTTP {http_data['status_code']})"
    )
    if http_data["error"]:
        console.print(f"  [red]Error:[/red] {http_data['error']}")

    # 3. Vaults & Storage
    vaults_data = check_vaults_and_storage(wikis_dir)
    v_table = Table(title="Knowledge Vaults & Storage", show_header=True, header_style="bold blue")
    v_table.add_column("Vault", style="bold")
    v_table.add_column("Compiled Notes", justify="right")
    v_table.add_column("Pending Backlog", justify="right")
    v_table.add_column("Quarantined", justify="right")

    for v in vaults_data["vaults"]:
        q_style = f"[red]{v['quarantine_count']}[/red]" if v["quarantine_count"] > 0 else "[green]0[/green]"
        b_style = f"[yellow]{v['backlog_count']}[/yellow]" if v["backlog_count"] > 0 else "[dim]0[/dim]"
        v_table.add_row(v["name"], str(v["total_pages"]), b_style, q_style)
    console.print(v_table)

    disk = vaults_data["disk"]
    console.print(
        f"[bold]Disk Usage:[/bold] {disk['free_gb']} GB free / {disk['total_gb']} GB total "
        f"([cyan]{disk['used_percent']}% used[/cyan])\n"
    )

    # 4. Recent Journal Logs Scan
    logs_data = check_journal_logs(since_seconds=300)
    if logs_data["errors"]:
        console.print(f"[bold red]⚠️ Found {len(logs_data['errors'])} Error(s) in Last 5 Minutes:[/bold red]")
        for err in logs_data["errors"][:5]:
            console.print(f"  • [red][{err['service']}][/red] {err['summary']}")
    else:
        console.print("[green]✔ No unhandled errors or tracebacks in recent journal logs.[/green]")

    # 5. Rate Limit Cooldowns
    cd_data = check_provider_cooldowns()
    if cd_data.get("active_cooldowns"):
        console.print("\n[bold yellow]Active Provider Cooldowns:[/bold yellow]")
        for m, dur in cd_data["active_cooldowns"].items():
            console.print(f"  • [yellow]{m}[/yellow]: {dur}s remaining")


@monitor_cmd.command("check")
@click.option("--base-dir", "-b", default=str(DEFAULT_WIKIS_DIR), type=click.Path(), help="Base directory containing wiki vaults.")
@click.option("--alerts/--no-alerts", default=True, help="Send alerts to Discord when issues are found.")
def check_cmd(base_dir: str, alerts: bool):
    """Execute one diagnostic evaluation cycle, update MONITOR_REPORT.md, and alert."""
    runner = MonitorRunner(wikis_dir=Path(base_dir))
    result = runner.run_check(send_alerts=alerts)

    if result["healthy"]:
        console.print(f"[bold green]✔ Health check passed at {result['timestamp']}. All systems operational.[/bold green]")
    else:
        console.print(f"[bold red]⚠️ Health check detected {result['active_incidents_count']} active incident(s)![/bold red]")
        for inc in result["new_incidents"]:
            console.print(f"  • Alert dispatched: [bold red]{inc['title']}[/bold red]")


@monitor_cmd.command("report")
@click.option("--report-file", "-r", default=str(DEFAULT_REPORT_FILE), type=click.Path(), help="Path to report file.")
def report_cmd(report_file: str):
    """Display the generated MONITOR_REPORT.md dashboard."""
    p = Path(report_file)
    if not p.exists():
        console.print(f"[yellow]No report file found at {p}. Run 'wiki monitor check' first.[/yellow]")
        return
    console.print(p.read_text(encoding="utf-8"))


@monitor_cmd.command("test-alert")
def test_alert_cmd():
    """Send a test notification to the user's Discord home channel."""
    console.print("Sending test notification to Discord home channel via Hermes gateway credentials...")
    success = send_discord_alert(
        title="🔔 LLM Wiki System Monitor — Test Alert",
        description="This is a test notification confirming that the LLM Wiki health monitor is successfully wired to your Discord home channel.",
        fields=[
            {"name": "Status", "value": "ONLINE & READY", "inline": True},
            {"name": "Triggered By", "value": "wiki monitor test-alert", "inline": True},
        ],
        severity="info",
    )
    if success:
        console.print("[bold green]✔ Test notification sent successfully to Discord![/bold green]")
    else:
        console.print("[bold red]❌ Failed to send test notification. Check ~/.hermes/.env credentials.[/bold red]")


@monitor_cmd.command("daemon")
@click.option("--interval", "-i", default=300, type=int, help="Check frequency in seconds (default: 300 / 5 minutes).")
@click.option("--base-dir", "-b", default=str(DEFAULT_WIKIS_DIR), type=click.Path(), help="Base directory containing wiki vaults.")
@click.option("--alerts/--no-alerts", default=True, help="Send alerts to Discord when issues are found.")
def daemon_cmd(interval: int, base_dir: str, alerts: bool):
    """Run continuous 24/7 background monitoring loop (used by systemd service)."""
    wikis_dir = Path(base_dir)
    runner = MonitorRunner(wikis_dir=wikis_dir)
    console.print(f"[bold cyan]Starting LLM Wiki Monitor Daemon (interval: {interval}s)...[/bold cyan]")

    while True:
        try:
            res = runner.run_check(since_seconds=interval + 30, send_alerts=alerts)
            status_str = "HEALTHY" if res["healthy"] else f"{res['active_incidents_count']} INCIDENT(S)"
            console.print(f"[{res['timestamp']}] Check completed: {status_str}")
        except Exception as e:
            console.print(f"[bold red]Daemon error during check:[/bold red] {e}")

        time.sleep(interval)
