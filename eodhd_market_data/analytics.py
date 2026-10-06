"""Simple-return statistics over a series of end-of-day bars."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date

from eodhd_market_data.models import Bar

PERIODS_PER_YEAR = {"d": 252, "w": 52, "m": 12}


@dataclass(frozen=True)
class ReturnsSummary:
    start: date
    end: date
    observations: int
    first_close: float
    last_close: float
    total_return: float
    mean_return: float
    volatility: float | None
    annualized_volatility: float | None
    max_drawdown: float
    best: tuple[date, float]
    worst: tuple[date, float]


def periods_per_year(ticker: str, period: str = "d") -> int:
    """Crypto trades every day, so daily bars annualize over 365 instead of 252."""
    if period == "d" and ticker.upper().endswith(".CC"):
        return 365
    return PERIODS_PER_YEAR[period]


def simple_returns(bars: list[Bar]) -> list[tuple[date, float]]:
    """r_t = P_t / P_{t-1} - 1 on adjusted closes, so splits and dividends don't show up as moves."""
    out = []
    for prev, cur in zip(bars, bars[1:]):
        if prev.adjusted_close:
            out.append((cur.date, cur.adjusted_close / prev.adjusted_close - 1.0))
    return out


def max_drawdown(prices: list[float]) -> float:
    """Largest peak-to-trough fall, as a negative fraction (0.0 if prices never fell)."""
    peak = -math.inf
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            worst = min(worst, price / peak - 1.0)
    return worst


def summarize(bars: list[Bar], annualization: int = 252) -> ReturnsSummary:
    if len(bars) < 2:
        raise ValueError("need at least two bars to compute returns")
    bars = sorted(bars, key=lambda b: b.date)
    rets = simple_returns(bars)
    values = [r for _, r in rets]
    vol = statistics.stdev(values) if len(values) > 1 else None
    first, last = bars[0].adjusted_close, bars[-1].adjusted_close
    return ReturnsSummary(
        start=bars[0].date,
        end=bars[-1].date,
        observations=len(values),
        first_close=first,
        last_close=last,
        total_return=last / first - 1.0,
        mean_return=statistics.fmean(values),
        volatility=vol,
        annualized_volatility=vol * math.sqrt(annualization) if vol is not None else None,
        max_drawdown=max_drawdown([b.adjusted_close for b in bars]),
        best=max(rets, key=lambda x: x[1]),
        worst=min(rets, key=lambda x: x[1]),
    )
