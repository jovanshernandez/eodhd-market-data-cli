from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from conftest import BARS, FakeResponse, FakeSession
from eodhd_market_data import cli
from eodhd_market_data.client import EodhdClient


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession()
    real_init = EodhdClient.__init__

    def init(self, api_key, **kwargs):
        kwargs["session"] = fake
        kwargs["sleep"] = lambda _: None
        real_init(self, api_key, **kwargs)

    monkeypatch.setattr(EodhdClient, "__init__", init)
    monkeypatch.setenv("EODHD_API_KEY", "secret-key")
    return fake


def test_prices_table_with_summary(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=BARS))
    assert cli.main(["prices", "AAPL.US", "--from", "2026-09-01", "--to", "2026-09-04", "--summary"]) == 0
    out = capsys.readouterr().out
    assert "2026-09-04" in out and "+10.00%" in out
    assert "Total return" in out and "+20.00%" in out
    assert "Max drawdown" in out and "-10.00%" in out
    assert "\033[" not in out  # no color when stdout is not a TTY


def test_prices_csv_to_file(session: FakeSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=BARS))
    target = tmp_path / "out" / "aapl.csv"
    assert cli.main(["prices", "AAPL.US", "-o", str(target)]) == 0
    rows = list(csv.DictReader(target.open()))
    assert rows[0] == {"date": "2026-09-01", "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.0",
                       "adjusted_close": "100.0", "volume": "1000"}
    assert "wrote 4 rows" in capsys.readouterr().err


def test_prices_json_with_summary_is_one_document(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=BARS))
    assert cli.main(["prices", "AAPL.US", "--format", "json", "--summary"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ticker"] == "AAPL.US"
    assert len(doc["bars"]) == 4
    assert doc["summary"]["total_return"] == pytest.approx(0.2)
    assert doc["summary"]["worst"]["date"] == "2026-09-03"


def test_prices_csv_stdout_keeps_summary_on_stderr(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=BARS))
    assert cli.main(["prices", "AAPL.US", "--format", "csv", "--summary"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "date,open,high,low,close,adjusted_close,volume"
    assert "Total return" in captured.err


def test_quote_table(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=[
        {"code": "AAPL.US", "timestamp": 1791232020, "close": 332.89, "change": -0.8, "change_p": -0.2397},
        {"code": "EURUSD.FOREX", "timestamp": 1791256080, "close": 1.1217, "change": -0.0037, "change_p": -0.331},
    ]))
    assert cli.main(["quote", "AAPL.US", "EURUSD.FOREX", "--color", "always"]) == 0
    out = capsys.readouterr().out
    assert "AAPL.US" in out and "-0.24%" in out and "-0.00370" in out
    assert "\033[31m" in out  # negative change rendered red


def test_search_json(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.append(FakeResponse(payload=[{"Code": "VTI", "Exchange": "US", "Name": "Vanguard Total Stock Market ETF", "Type": "ETF"}]))
    assert cli.main(["search", "vanguard total", "--type", "etf", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["code"] == "VTI"
    assert session.calls[0]["params"]["type"] == "etf"


def test_api_error_exits_1_without_leaking_key(session: FakeSession, capsys: pytest.CaptureFixture[str]) -> None:
    session.queue.extend(FakeResponse(503) for _ in range(4))
    assert cli.main(["quote", "AAPL.US"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 503" in err and "retry 3/3" in err
    assert "secret-key" not in err


def test_missing_api_key_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["quote", "AAPL.US"])
    assert exc.value.code == 2
    assert "EODHD_API_KEY" in capsys.readouterr().err


def test_bad_date_is_a_usage_error(session: FakeSession) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["prices", "AAPL.US", "--from", "09/01/2026"])
    assert exc.value.code == 2
