"""Regression coverage for concise OpenAI profile picker descriptions."""

from unittest import mock

import pytest


@pytest.mark.unit
def test_profile_menu_explains_primary_model_routing(monkeypatch):
    import cli.utils as utils

    prompt = mock.Mock()
    prompt.ask.return_value = "balanced"
    select = mock.Mock(return_value=prompt)
    monkeypatch.setattr(utils.questionary, "select", select)

    assert utils.select_model_profile() == "balanced"

    kwargs = select.call_args.kwargs
    choices = kwargs["choices"]
    assert [(choice.title, choice.value) for choice in choices] == [
        ("Quick — Mostly Luna; Terra decisions — 1 debate / 1 risk rounds", "quick"),
        (
            "Balanced — Mostly Terra; Sol research; Astra final — "
            "2 debate / 2 risk rounds",
            "balanced",
        ),
        (
            "Deep — Terra specialists; Sol debate; Astra managers — "
            "3 debate / 3 risk rounds",
            "deep",
        ),
        ("Custom — choose research depth, models, and reasoning effort", "custom"),
    ]
    assert kwargs["default"] == "balanced"


@pytest.mark.unit
def test_profile_menu_preserves_explicit_default_and_cancellation(monkeypatch):
    import cli.utils as utils

    prompt = mock.Mock()
    prompt.ask.return_value = None
    select = mock.Mock(return_value=prompt)
    monkeypatch.setattr(utils.questionary, "select", select)

    with pytest.raises(SystemExit, match="1"):
        utils.select_model_profile(default="custom")

    assert select.call_args.kwargs["default"] == "custom"
