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
    monkeypatch.setattr(main, "run_codex_analysis", Mock(side_effect=AssertionError("Codex started")))
    result = CliRunner().invoke(main.app, args)
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(checkpoint=None)


def test_codex_route_never_enters_api_flow(monkeypatch):
    setup = Mock()
    monkeypatch.setattr(main, "run_codex_analysis", setup)
    monkeypatch.setattr(main, "run_analysis", Mock(side_effect=AssertionError("API flow entered")))
    result = CliRunner().invoke(main.app, ["--backend", "codex"])
    assert result.exit_code == 0, result.output
    setup.assert_called_once_with(checkpoint=None, clear_checkpoints=False)


def test_explicit_flag_wins_over_backend_environment(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BACKEND", "codex")
    run = Mock()
    monkeypatch.setattr(main, "run_analysis", run)
    monkeypatch.setattr(main, "run_codex_analysis", Mock(side_effect=AssertionError("Codex started")))
    result = CliRunner().invoke(main.app, ["--backend", "api", "--no-checkpoint"])
    assert result.exit_code == 0
    run.assert_called_once_with(checkpoint=False)


def test_backend_environment_skips_picker(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BACKEND", "codex")
    setup = Mock()
    monkeypatch.setattr(main, "run_codex_analysis", setup)
    monkeypatch.setattr(main, "select_backend", Mock(side_effect=AssertionError("Unexpected prompt")))
    result = CliRunner().invoke(main.app, [])
    assert result.exit_code == 0
    setup.assert_called_once()


@pytest.mark.parametrize("flag, checkpoint, clear", [
    ("--checkpoint", True, False), ("--no-checkpoint", False, False),
    ("--clear-checkpoints", None, True),
])
def test_codex_routes_checkpoint_options_to_codex_only(monkeypatch, flag, checkpoint, clear):
    run = Mock()
    monkeypatch.setattr(main, "run_codex_analysis", run)
    import tradingagents.graph.checkpointer as checkpointer
    monkeypatch.setattr(checkpointer, "clear_all_checkpoints", Mock(side_effect=AssertionError("Deleted API state")))
    result = CliRunner().invoke(main.app, ["--backend", "codex", flag])
    assert result.exit_code == 0
    run.assert_called_once_with(checkpoint=checkpoint, clear_checkpoints=clear)


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

        def __init__(self, *, home, timeout=300):
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


def test_codex_full_run_owns_runtime_and_clears_only_codex_checkpoints(monkeypatch, tmp_path):
    fake = _fake_adapter(monkeypatch)
    monkeypatch.setitem(main.DEFAULT_CONFIG, "data_cache_dir", str(tmp_path))
    api_dir = tmp_path / "checkpoints"
    codex_dir = tmp_path / "codex" / "checkpoints"
    api_dir.mkdir()
    codex_dir.mkdir(parents=True)
    (api_dir / "AMD.db").write_text("API state")
    (codex_dir / "AMD.db").write_text("Codex state")
    selection = {"ticker": "AMD"}
    monkeypatch.setattr(main, "get_user_selections", lambda **kwargs: selection)
    run = Mock()
    monkeypatch.setattr(main, "run_analysis", run)
    main.run_codex_analysis(checkpoint=True, clear_checkpoints=True)
    run.assert_called_once_with(checkpoint=True, selections=selection, codex_adapter=fake.instances[0])
    assert fake.instances[0].closed
    assert (api_dir / "AMD.db").read_text() == "API state"
    assert not (codex_dir / "AMD.db").exists()


def test_codex_runtime_closes_after_analysis_failure(monkeypatch):
    fake = _fake_adapter(monkeypatch)
    from tradingagents.codex.adapter import CodexAdapterError
    monkeypatch.setattr(main, "get_user_selections", lambda **kwargs: {})
    run = Mock(side_effect=CodexAdapterError("offline failure"))
    monkeypatch.setattr(main, "run_analysis", run)
    with pytest.raises(typer.Exit):
        main.run_codex_analysis()
    assert run.call_count == 1
    assert fake.instances[0].closed


def test_codex_research_profile_rejects_unsupported_catalog(monkeypatch):
    fake = _fake_adapter(monkeypatch, available=False)
    from tradingagents.codex.adapter import CodexAdapterError
    with fake(home="unused") as adapter, pytest.raises(CodexAdapterError, match="No complete research profile"):
        backend.select_codex_profile(adapter)


def test_old_sol_luna_catalog_does_not_enable_current_profiles(monkeypatch):
    from tradingagents.codex.adapter import CodexAdapterError

    class OldCatalog:
        def validate_selection(self, model, effort):
            if model not in {"gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"}:
                raise CodexAdapterError("model unavailable")

    adapter = OldCatalog()
    assert all(backend._profile_issues(adapter, name) for name in ("quick", "balanced", "deep"))
    with pytest.raises(CodexAdapterError, match="No complete research profile"):
        backend.select_codex_profile(adapter)


def test_observed_high_efforts_alone_do_not_enable_profiles():
    from tradingagents.codex.adapter import CodexAdapterError

    observed = {
        ("gpt-6-sol", "high"), ("gpt-6-sol", "xhigh"),
        ("gpt-6-luna", "high"), ("gpt-6-astra", "high"),
        ("gpt-5.6-terra", "medium"), ("gpt-5.6-terra", "high"),
    }

    class PartialCatalog:
        def validate_selection(self, model, effort):
            if (model, effort) not in observed:
                raise CodexAdapterError("effort unavailable")

    adapter = PartialCatalog()
    assert "market" in backend._profile_issues(adapter, "quick")
    assert "trader" in backend._profile_issues(adapter, "balanced")
    assert "bull" in backend._profile_issues(adapter, "deep")
    with pytest.raises(CodexAdapterError, match="No complete research profile"):
        backend.select_codex_profile(adapter)


def test_codex_selections_skip_api_settings(monkeypatch):
    from cli.models import AnalystType
    fake = _fake_adapter(monkeypatch)
    for name in ("TRADINGAGENTS_OUTPUT_LANGUAGE",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(main, "fetch_announcements", lambda: None)
    monkeypatch.setattr(main, "display_announcements", lambda *a: None)
    monkeypatch.setattr(main, "get_ticker", lambda: "AMD")
    monkeypatch.setattr(main, "get_analysis_date", lambda: "2026-09-13")
    monkeypatch.setattr(main, "ask_output_language", lambda: "English")
    monkeypatch.setattr(main, "select_analysts", lambda *a: [AnalystType.FUNDAMENTALS])
    monkeypatch.setattr(main, "ensure_api_key", Mock(side_effect=AssertionError("API key prompt")))
    monkeypatch.setattr(main, "select_llm_provider", Mock(side_effect=AssertionError("API provider prompt")))
    monkeypatch.setattr(backend, "_ask", lambda *a, **k: "balanced")
    with fake(home="unused") as adapter:
        selections = main.get_user_selections(codex_adapter=adapter)
    config = main._build_run_config(selections, checkpoint=True)
    assert config["llm_backend"] == "codex"
    assert config["backend_url"] is None
    assert config["checkpoint_enabled"] is True
    assert config["agent_models"]["fundamentals"] == {"model": "gpt-6-sol", "reasoning_effort": "high"}
