from __future__ import annotations

import math
from datetime import date

import pytest

from conftest import BARS
from eodhd_market_data.analytics import max_drawdown, periods_per_year, simple_returns, summarize
from eodhd_market_data.models import Bar

bars = [Bar.from_api(item) for item in BARS]  # adjusted closes: 100, 110, 99, 120


def test_simple_returns() -> None:
    rets = [r for _, r in simple_returns(bars)]
    assert rets == pytest.approx([0.10, -0.10, 120 / 99 - 1])


def test_max_drawdown_is_peak_to_trough() -> None:
    assert max_drawdown([100, 110, 99, 120]) == pytest.approx(-0.10)
    assert max_drawdown([1, 2, 3]) == 0.0


def test_summarize() -> None:
    s = summarize(bars, annualization=252)
    assert s.total_return == pytest.approx(0.20)
    assert s.observations == 3
    assert s.best[0] == date(2026, 9, 4)
    assert s.worst == (date(2026, 9, 3), pytest.approx(-0.10))
    assert s.annualized_volatility == pytest.approx(s.volatility * math.sqrt(252))


def test_summarize_uses_adjusted_close() -> None:
    split = [
        Bar(date(2026, 1, 1), 200, 200, 200, 200, 100, 0),  # pre-split price, adjusted back to 100
        Bar(date(2026, 1, 2), 101, 101, 101, 101, 101, 0),
    ]
    assert summarize(split).total_return == pytest.approx(0.01)


def test_summarize_needs_two_bars() -> None:
    with pytest.raises(ValueError):
        summarize(bars[:1])


def test_periods_per_year() -> None:
    assert periods_per_year("AAPL.US") == 252
    assert periods_per_year("BTC-USD.CC") == 365
    assert periods_per_year("BTC-USD.CC", "w") == 52
    assert periods_per_year("VTI.US", "m") == 12
