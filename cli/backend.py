"""Startup backend selection; importing this module never starts Codex."""

from __future__ import annotations

import os
from enum import Enum

import questionary
import typer
from rich.console import Console
from rich.table import Table

from cli.utils import PROFILE_MENU_DESCRIPTIONS
from tradingagents.model_profiles import MODEL_PROFILES


class Backend(str, Enum):
    API = "api"
    CODEX = "codex"


def _ask(prompt: str, choices: list, default=None):
    value = questionary.select(prompt, choices=choices, default=default).ask()
    if value is None:
        raise typer.Exit(code=0)
    return value


def select_backend() -> Backend:
    return Backend(_ask(
        "How would you like to use TradingAgents?",
        [
            questionary.Choice("API — run analysis with your existing provider", value="api"),
            questionary.Choice("Codex subscription — run analysis with ChatGPT sign-in", value="codex"),
        ],
        default="api",
    ))


def _profile_issues(adapter, name: str) -> list[str]:
    from tradingagents.codex.adapter import CodexAdapterError

    issues = []
    # Signal extraction is deterministic and reflection is outside an analysis run.
    for role, setting in MODEL_PROFILES[name]["agents"].items():
        if role in {"signal", "reflection"}:
            continue
        try:
            adapter.validate_selection(setting["model"], setting["reasoning_effort"])
        except CodexAdapterError:
            issues.append(role)
    return issues


def run_codex_setup(console: Console) -> None:
    """Read subscription/model metadata only; inference requires the explicit pilot option."""
    from tradingagents.codex.adapter import CodexAdapter, CodexAdapterError
    from tradingagents.codex.transport import TransportError

    console.print("\n[bold]Codex subscription setup — preview[/bold]")
    console.print(
        "This checks sign-in and model settings. Use --backend codex --fundamentals for the fundamentals-only pilot."
    )
    home = os.environ.get("TRADINGAGENTS_CODEX_HOME", "~/.tradingagents/codex")
    try:
        with CodexAdapter(home=home) as adapter:
            models = adapter.list_models()
            console.print("[green]ChatGPT sign-in verified.[/green]")
            table = Table("Model", "Supported reasoning efforts")
            for model in models:
                table.add_row(model.model, ", ".join(model.supported_efforts))
            console.print(table)

            issues = {name: _profile_issues(adapter, name) for name in MODEL_PROFILES}
            choices = [
                questionary.Choice(
                    f"{profile['label']} — {PROFILE_MENU_DESCRIPTIONS[name]}",
                    value=name,
                    disabled=("Unavailable for: " + ", ".join(issues[name])) if issues[name] else None,
                )
                for name, profile in MODEL_PROFILES.items()
            ]
            choices.append(questionary.Choice("Custom — check one model and effort", value="custom"))
            choice = _ask("Which model settings would you like to check?", choices,
                          default="balanced" if not issues["balanced"] else "custom")
            if choice == "custom":
                by_model = {model.model: model for model in models}
                selected = _ask("Model", list(by_model))
                model = by_model[selected]
                effort = _ask("Reasoning effort", list(model.supported_efforts),
                              default=model.default_effort)
                adapter.validate_selection(selected, effort)
                console.print(f"[green]Catalog match:[/green] {selected}, {effort} effort")
            else:
                if choice not in MODEL_PROFILES or _profile_issues(adapter, choice):
                    raise CodexAdapterError("The selected profile is not supported by this catalog")
                profile = MODEL_PROFILES[choice]
                console.print(
                    f"[green]Catalog match:[/green] {profile['label']} — "
                    f"{profile['rounds']} debate / {profile['rounds']} risk rounds"
                )
    except (CodexAdapterError, TransportError) as exc:
        console.print(f"Codex setup could not complete: {exc}", markup=False)
        console.print(
            "Install/update the official Codex CLI, then sign in to the dedicated runtime "
            "with ChatGPT. See docs/CODEX-INTEGRATION.md for setup steps. "
            "API analysis remains available with --backend api."
        )
        raise typer.Exit(code=1) from None

    console.print(
        "\nSetup check complete. No analysis was run and no settings were saved. "
        "Model access and available usage still need a user-run inference check. "
        "Use --backend codex --fundamentals for the pilot, or --backend api for the full pipeline."
    )


def select_codex_profile(adapter) -> str:
    """Offer only complete, advertised research profiles; never downgrade a role."""
    from tradingagents.codex.adapter import CodexAdapterError

    issues = {name: _profile_issues(adapter, name) for name in MODEL_PROFILES}
    available = [name for name in MODEL_PROFILES if not issues[name]]
    if not available:
        raise CodexAdapterError("No complete research profile is supported by this catalog")
    choices = [questionary.Choice(
        f"{profile['label']} — {PROFILE_MENU_DESCRIPTIONS[name]}", value=name,
        disabled=("Unavailable for: " + ", ".join(issues[name])) if issues[name] else None,
    ) for name, profile in MODEL_PROFILES.items()]
    selected = _ask("Codex Agent Profile", choices,
                    default="balanced" if "balanced" in available else available[0])
    if selected not in available:
        raise CodexAdapterError("The selected profile is not supported by this catalog")
    return selected
