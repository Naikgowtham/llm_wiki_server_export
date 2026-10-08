"""Interactive setup command to configure LLM providers and API keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from lib.config import find_providers_config

console = Console()

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "llm-wiki"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "providers.yaml"
LOCAL_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "providers.yaml"

DEFAULT_MODELS = {
    "anthropic": ["claude-sonnet-4-20250514", "claude-haiku-35-20250620"],
    "openai": ["gpt-4o", "gpt-4o-mini"],
    "google": ["gemini/gemini-2.5-pro", "gemini/gemini-2.5-flash"],
    "ollama": ["ollama/qwen3:32b", "ollama/llama3.3:latest"],
    "openrouter": ["openrouter/anthropic/claude-3.5-sonnet", "openrouter/openai/gpt-4o"],
    "groq": ["groq/llama-3.3-70b-versatile"],
    "deepseek": ["deepseek/deepseek-chat"],
}


def load_existing_config() -> tuple[Dict[str, Any], Path]:
    """Load existing configuration from global or local path, or initialize a new dict."""
    target_path = GLOBAL_CONFIG_FILE
    if LOCAL_CONFIG_FILE.exists():
        target_path = LOCAL_CONFIG_FILE
    elif find_providers_config():
        target_path = find_providers_config()

    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data, target_path
        except Exception:
            pass

    return {
        "providers": {},
        "fallback_chain": {
            "ingest_extract": [],
            "ingest_synth": [],
            "ingest_crossref": [],
            "query_answer": [],
            "query_fileback": [],
            "lint_audit": [],
            "lint_fix": [],
            "embeddings": ["local/ollama/nomic-embed-text", "google/gemini/text-embedding-004"],
        },
        "token_limits": {
            "ingest_chunk": 8000,
            "query_context": 16000,
            "lint_batch": 32000,
        },
    }, target_path


def print_configured_providers(config: Dict[str, Any]):
    """Display currently saved providers."""
    providers = config.get("providers", {})
    if not providers:
        console.print("[yellow]No providers configured yet.[/yellow]\n")
        return

    table = Table(title="Configured LLM Providers", show_header=True, header_style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Base URL", style="dim")
    table.add_column("API Key Status")
    table.add_column("Models")

    for p_name, p_data in providers.items():
        base_url = p_data.get("api_base", "Default API URL")
        key = p_data.get("api_key", "")
        if key:
            key_status = f"[green]Set ({key[:4]}...{key[-4:] if len(key) > 8 else ''})[/green]"
        else:
            key_status = "[dim]None (Local)[/dim]"
        models = ", ".join(p_data.get("models", [])) or "-"
        table.add_row(p_name, base_url, key_status, models)

    console.print(table)
    console.print()


def save_config(config: Dict[str, Any], target_path: Path):
    """Write configuration safely to yaml file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


@click.command("setup")
@click.option("--global/--local", "is_global", default=True, help="Save to global ~/.config/llm-wiki/ or project config.")
def setup_cmd(is_global: bool):
    """Interactive loop to configure LLM providers, URLs, and API keys."""
    console.print(Panel(
        "[bold cyan]LLM Wiki — Interactive Provider Setup[/bold cyan]\n\n"
        "Configure your LLM providers (Anthropic, OpenAI, Google, Ollama, OpenRouter, etc.).\n"
        "Press [bold red]Ctrl+C[/bold red] at any prompt to finish and save.",
        border_style="cyan",
    ))

    config, current_path = load_existing_config()
    target_path = GLOBAL_CONFIG_FILE if is_global else LOCAL_CONFIG_FILE

    print_configured_providers(config)

    added_count = 0

    try:
        while True:
            console.print("[bold yellow]Add / Update Provider:[/bold yellow] (or press Ctrl+C when finished)")

            provider_name = Prompt.ask(
                "  [1] Provider name (e.g. openai, anthropic, google, ollama, openrouter, groq)"
            ).strip().lower()

            if not provider_name:
                continue

            provider_url = Prompt.ask(
                "  [2] Provider URL / Base URL (press Enter for default)",
                default=""
            ).strip()

            api_key = Prompt.ask(
                "  [3] API Key (press Enter if local / not required)",
                password=True,
                default=""
            ).strip()

            default_model_list = DEFAULT_MODELS.get(provider_name, [f"{provider_name}/default"])
            models_input = Prompt.ask(
                "  [4] Models (comma-separated)",
                default=", ".join(default_model_list)
            ).strip()

            models = [m.strip() for m in models_input.split(",") if m.strip()]

            # Build provider object
            p_data: Dict[str, Any] = {"models": models}
            if provider_url:
                p_data["api_base"] = provider_url
            if api_key:
                p_data["api_key"] = api_key

            config.setdefault("providers", {})[provider_name] = p_data

            # Update fallback chains
            for op in ("ingest_extract", "ingest_synth", "ingest_crossref", "query_answer", "query_fileback", "lint_audit", "lint_fix", "embeddings"):
                chain = config.setdefault("fallback_chain", {}).setdefault(op, [])
                for m in models:
                    if m not in chain:
                        chain.append(m)

            save_config(config, target_path)
            added_count += 1
            console.print(f"[bold green]✓ Provider '{provider_name}' saved to {target_path}![/bold green]\n")

    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Setup loop closed.[/yellow]\n")

    # Final summary
    console.print(f"[bold green]Configuration successfully saved to:[/bold green] {target_path}")
    print_configured_providers(config)
