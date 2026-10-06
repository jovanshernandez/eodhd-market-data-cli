"""Typed records parsed from EODHD JSON payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SearchResult:
    code: str
    exchange: str
    name: str
    type: str | None = None
    country: str | None = None
    currency: str | None = None
    isin: str | None = None
    previous_close: float | None = None

    @property
    def ticker(self) -> str:
        return f"{self.code}.{self.exchange}" if self.exchange else self.code

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> SearchResult:
        return cls(
            code=str(item.get("Code") or "").upper(),
            exchange=str(item.get("Exchange") or "").upper(),
            name=str(item.get("Name") or ""),
            type=item.get("Type"),
            country=item.get("Country"),
            currency=item.get("Currency"),
            isin=item.get("ISIN"),
            previous_close=_float(item.get("previousClose")),
        )


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Bar:
        close = float(item["close"])
        return cls(
            date=date.fromisoformat(item["date"]),
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=close,
            adjusted_close=float(item.get("adjusted_close") or close),
            volume=int(item.get("volume") or 0),
        )


@dataclass(frozen=True)
class Quote:
    ticker: str
    timestamp: datetime | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    previous_close: float | None
    change: float | None
    change_pct: float | None
    volume: int | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Quote:
        ts = item.get("timestamp")
        return cls(
            ticker=str(item.get("code") or ""),
            timestamp=datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None,
            open=_float(item.get("open")),
            high=_float(item.get("high")),
            low=_float(item.get("low")),
            close=_float(item.get("close")),
            previous_close=_float(item.get("previousClose")),
            change=_float(item.get("change")),
            change_pct=_float(item.get("change_p")),
            volume=int(item["volume"]) if isinstance(item.get("volume"), (int, float)) else None,
        )
