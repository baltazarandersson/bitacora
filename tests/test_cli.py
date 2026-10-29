from __future__ import annotations

import json

import pytest

from bitacora import cli, diagnostics


@pytest.fixture
def collect_spy(monkeypatch):
    calls = []

    def fake_collect(**kwargs):
        calls.append(kwargs)
        return {"hours": kwargs["hours"]}

    monkeypatch.setattr(diagnostics, "collect", fake_collect)
    return calls


def test_diagnostics_pass_hours_to_collect(collect_spy, capsys):
    cli.main(["--diagnostics", "--hours", "5"])

    assert len(collect_spy) == 1
    assert collect_spy[0]["hours"] == 5
    assert json.loads(capsys.readouterr().out) == {"hours": 5}


def test_diagnostics_use_24_hours_by_default(collect_spy):
    cli.main(["--diagnostics"])

    assert len(collect_spy) == 1
    assert collect_spy[0]["hours"] == 24


@pytest.mark.parametrize("value", ["100", "inf"])
def test_hours_are_capped_at_72(value, collect_spy):
    cli.main(["--diagnostics", "--hours", value])

    assert collect_spy[0]["hours"] == 72


@pytest.mark.parametrize("value", ["0", "-1", "nan"])
def test_invalid_hours_stop_with_usage_error(value, collect_spy, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--diagnostics", "--hours", value])

    assert excinfo.value.code == 2
    assert collect_spy == []
    assert "--hours must be a positive number" in capsys.readouterr().err
