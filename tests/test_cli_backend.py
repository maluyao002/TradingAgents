"""Startup routes remain exclusive; Codex setup cannot trigger API analysis."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

import cli.backend as backend
import cli.main as main


@pytest.fixture(autouse=True)
def _isolate_backend_env(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_BACKEND", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_CODEX_HOME", raising=False)


@pytest.mark.parametrize("args", [["--backend", "api"], []])
def test_api_route_preserves_run_analysis(monkeypatch, args):
    run = Mock()
    monkeypatch.setattr(main, "run_analysis", run)
    monkeypatch.setattr(main, "select_backend", lambda: backend.Backend.API)
    monkeypatch.setattr(main, "run_codex_setup", Mock(side_effect=AssertionError("Codex started")))
    result = CliRunner().invoke(main.app, args)
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(checkpoint=None)


def test_codex_route_never_enters_api_flow(monkeypatch):
    setup = Mock()
    monkeypatch.setattr(main, "run_codex_setup", setup)
    monkeypatch.setattr(main, "run_analysis", Mock(side_effect=AssertionError("API flow entered")))
    result = CliRunner().invoke(main.app, ["--backend", "codex"])
    assert result.exit_code == 0, result.output
    setup.assert_called_once_with(main.console)


def test_explicit_flag_wins_over_backend_environment(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BACKEND", "codex")
    run = Mock()
    monkeypatch.setattr(main, "run_analysis", run)
    monkeypatch.setattr(main, "run_codex_setup", Mock(side_effect=AssertionError("Codex started")))
    result = CliRunner().invoke(main.app, ["--backend", "api", "--no-checkpoint"])
    assert result.exit_code == 0
    run.assert_called_once_with(checkpoint=False)


def test_backend_environment_skips_picker(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BACKEND", "codex")
    setup = Mock()
    monkeypatch.setattr(main, "run_codex_setup", setup)
    monkeypatch.setattr(main, "select_backend", Mock(side_effect=AssertionError("Unexpected prompt")))
    result = CliRunner().invoke(main.app, [])
    assert result.exit_code == 0
    setup.assert_called_once()


@pytest.mark.parametrize("flag", ["--checkpoint", "--no-checkpoint", "--clear-checkpoints"])
def test_codex_rejects_api_checkpoint_options_before_side_effects(monkeypatch, flag):
    monkeypatch.setattr(main, "run_codex_setup", Mock(side_effect=AssertionError("Codex started")))
    import tradingagents.graph.checkpointer as checkpointer
    monkeypatch.setattr(checkpointer, "clear_all_checkpoints", Mock(side_effect=AssertionError("Deleted state")))
    result = CliRunner().invoke(main.app, ["--backend", "codex", flag])
    assert result.exit_code == 2
    assert "Checkpoint options apply to API analysis" in result.output


def test_unknown_backend_fails_before_any_analysis(monkeypatch):
    monkeypatch.setattr(main, "run_analysis", Mock(side_effect=AssertionError("API flow entered")))
    result = CliRunner().invoke(main.app, ["--backend", "typo"])
    assert result.exit_code == 2


def test_cancel_backend_picker_exits_cleanly(monkeypatch):
    monkeypatch.setattr(backend.questionary, "select", lambda *a, **k: SimpleNamespace(ask=lambda: None))
    with pytest.raises(typer.Exit) as caught:
        backend.select_backend()
    assert caught.value.exit_code == 0


def test_backend_picker_uses_existing_no_console_error_handler(monkeypatch):
    class MissingConsole(Exception):
        pass

    monkeypatch.setattr(main, "_NO_CONSOLE_ERRORS", (MissingConsole,))
    monkeypatch.setattr(main, "select_backend", Mock(side_effect=MissingConsole("private details")))
    result = CliRunner().invoke(main.app, [])
    assert result.exit_code == 1
    assert "no Windows console available" in result.output
    assert "private details" not in result.output


def _fake_adapter(monkeypatch, *, available=True, fail_auth=False):
    import tradingagents.codex.adapter as module

    class FakeAdapter:
        instances = []

        def __init__(self, *, home):
            self.home = home
            self.closed = False
            self.selections = []
            self.instances.append(self)

        def __enter__(self):
            if fail_auth:
                raise module.CodexAdapterError("Sign in with ChatGPT in the dedicated runtime")
            return self

        def __exit__(self, *args):
            self.closed = True

        def list_models(self):
            return (SimpleNamespace(model="test-model", default_effort="medium",
                                    supported_efforts=("low", "medium")),)

        def validate_selection(self, model, effort):
            self.selections.append((model, effort))
            if not available and model != "test-model":
                raise module.CodexAdapterError("Not in catalog")

        def complete(self, *args, **kwargs):
            raise AssertionError("Stage 2 setup must not start inference")

    monkeypatch.setattr(module, "CodexAdapter", FakeAdapter)
    return FakeAdapter


def test_codex_setup_checks_profile_without_inference(monkeypatch):
    fake = _fake_adapter(monkeypatch)
    monkeypatch.setattr(backend, "_ask", lambda *a, **k: "balanced")
    out = StringIO()
    backend.run_codex_setup(Console(file=out))
    assert "Catalog match: Balanced" in out.getvalue()
    assert "No analysis was run" in out.getvalue()
    assert fake.instances[0].closed
    assert fake.instances[0].home == "~/.tradingagents/codex"


def test_custom_only_shows_advertised_models_and_efforts(monkeypatch):
    fake = _fake_adapter(monkeypatch, available=False)
    answers = iter(["custom", "test-model", "medium"])

    def choose(prompt, choices, default=None):
        if prompt == "Model":
            assert choices == ["test-model"]
        elif prompt == "Reasoning effort":
            assert choices == ["low", "medium"]
        else:
            assert all(choice.disabled for choice in choices[:-1])
            assert default == "custom"
        return next(answers)

    monkeypatch.setattr(backend, "_ask", choose)
    backend.run_codex_setup(Console(file=StringIO()))
    assert fake.instances[0].selections[-1] == ("test-model", "medium")


def test_unsupported_profile_is_not_silently_downgraded(monkeypatch):
    fake = _fake_adapter(monkeypatch, available=False)
    monkeypatch.setattr(backend, "_ask", lambda *a, **k: "balanced")
    with pytest.raises(typer.Exit) as caught:
        backend.run_codex_setup(Console(file=StringIO()))
    assert caught.value.exit_code == 1
    assert fake.instances[0].closed


def test_missing_codex_auth_never_falls_back_to_api(monkeypatch):
    _fake_adapter(monkeypatch, fail_auth=True)
    monkeypatch.setattr(main, "run_analysis", Mock(side_effect=AssertionError("API started")))
    result = CliRunner().invoke(main.app, ["--backend", "codex"])
    assert result.exit_code == 1
    assert "Sign in with ChatGPT" in result.output
    assert "Traceback" not in result.output


def test_cancel_codex_selection_closes_adapter(monkeypatch):
    fake = _fake_adapter(monkeypatch)
    monkeypatch.setattr(backend, "_ask", Mock(side_effect=typer.Exit(code=0)))
    with pytest.raises(typer.Exit):
        backend.run_codex_setup(Console(file=StringIO()))
    assert fake.instances[0].closed
