"""Tests for the CLI."""

from __future__ import annotations

import pytest

from contact_center import get_parser, main
from contact_center._internal import debug, eval_runner


def test_main() -> None:
    """Basic CLI test."""
    assert main([]) == 0


def test_show_help(capsys: pytest.CaptureFixture) -> None:
    """Show help.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["-h"])
    captured = capsys.readouterr()
    assert "contact-center" in captured.out


def test_show_version(capsys: pytest.CaptureFixture) -> None:
    """Show version.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["-V"])
    captured = capsys.readouterr()
    assert debug._get_version() in captured.out


def test_show_debug_info(capsys: pytest.CaptureFixture) -> None:
    """Show debug information.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["--debug-info"])
    captured = capsys.readouterr().out.lower()
    assert "python" in captured
    assert "system" in captured
    assert "environment" in captured
    assert "packages" in captured


def test_chat_subcommand_parses() -> None:
    """The chat subcommand accepts a one-shot question and a customer id."""
    opts = get_parser().parse_args(["chat", "--question", "hi", "--customer", "KND-1002"])
    assert opts.command == "chat"
    assert opts.question == "hi"
    assert opts.customer == "KND-1002"


def test_chat_customer_defaults() -> None:
    """The customer id defaults to the demo customer."""
    assert get_parser().parse_args(["chat"]).customer == "KND-1001"


def test_chat_connect_flag_parses() -> None:
    """The chat subcommand accepts --connect."""
    opts = get_parser().parse_args(["chat", "--connect", "-q", "hi"])
    assert opts.connect is True


def test_eval_subcommand_parses() -> None:
    """The eval subcommand parses threshold and golden path."""
    opts = get_parser().parse_args(["eval", "--threshold", "0.9", "--golden", "g.json"])
    assert opts.command == "eval"
    assert opts.threshold == 0.9
    assert opts.golden == "g.json"


def test_eval_subcommand_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval subcommand dispatches to eval_runner.run_eval."""
    captured: dict = {}

    def _fake_run_eval(golden_path: str | None = None, *, threshold: float = 1.0) -> int:
        captured["golden"] = golden_path
        captured["threshold"] = threshold
        return 0

    monkeypatch.setattr(eval_runner, "run_eval", _fake_run_eval)
    assert main(["eval", "--threshold", "0.8"]) == 0
    assert captured == {"golden": None, "threshold": 0.8}
