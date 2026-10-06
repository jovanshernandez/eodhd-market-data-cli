"""Rendering: aligned terminal tables (optionally colored), CSV and JSON."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from eodhd_market_data.analytics import ReturnsSummary

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def wrap(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.enabled and text else text

    def signed(self, value: float | None, text: str) -> str:
        if value is None or value == 0:
            return text
        return self.wrap(GREEN if value > 0 else RED, text)


def fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.5f}" if abs(value) < 10 else f"{value:,.2f}"


def fmt_signed(value: float | None, ref: float | None = None) -> str:
    """Signed price move, using the decimals of the reference price (5 for FX, 2 otherwise)."""
    if value is None:
        return "-"
    return f"{value:+,.5f}" if abs(ref if ref is not None else value) < 10 else f"{value:+,.2f}"


def fmt_pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.2f}%" if signed else f"{value * 100:.2f}%"


def fmt_int(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _visible_len(text: str) -> int:
    length, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            i = text.index("m", i) + 1
            continue
        length += 1
        i += 1
    return length


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], align: str, style: Style) -> str:
    """Render rows as a fixed-width table. `align` is one 'l'/'r' char per column."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _visible_len(cell))

    def line(cells: Sequence[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            pad = " " * (widths[i] - _visible_len(cell))
            parts.append(cell + pad if align[i] == "l" else pad + cell)
        return "  ".join(parts).rstrip()

    out = [style.wrap(BOLD, line(headers)), style.wrap(DIM, "  ".join("-" * w for w in widths))]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def records(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [_plain(asdict(item)) if is_dataclass(item) else _plain(item) for item in items]


def to_json(data: Any) -> str:
    return json.dumps(_plain(data), indent=2) + "\n"


def to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def summary_dict(ticker: str, summary: ReturnsSummary) -> dict[str, Any]:
    data = _plain(asdict(summary))
    data["best"] = {"date": data["best"][0], "return": data["best"][1]}
    data["worst"] = {"date": data["worst"][0], "return": data["worst"][1]}
    return {"ticker": ticker, **data}


def summary_text(ticker: str, s: ReturnsSummary, annualization: int, style: Style) -> str:
    rows = [
        ("Period", f"{s.start} -> {s.end} ({s.observations} returns)"),
        ("Close", f"{fmt_price(s.first_close)} -> {fmt_price(s.last_close)}"),
        ("Total return", style.signed(s.total_return, fmt_pct(s.total_return))),
        ("Mean return", style.signed(s.mean_return, fmt_pct(s.mean_return))),
        ("Volatility", fmt_pct(s.volatility, signed=False)),
        (f"Annualized vol (x sqrt {annualization})", fmt_pct(s.annualized_volatility, signed=False)),
        ("Max drawdown", style.signed(s.max_drawdown, fmt_pct(s.max_drawdown))),
        ("Best", f"{s.best[0]}  {style.signed(s.best[1], fmt_pct(s.best[1]))}"),
        ("Worst", f"{s.worst[0]}  {style.signed(s.worst[1], fmt_pct(s.worst[1]))}"),
    ]
    width = max(len(k) for k, _ in rows)
    lines = [style.wrap(BOLD, f"Simple returns: {ticker} (adjusted close)")]
    lines += [f"  {k:<{width}}  {v}" for k, v in rows]
    return "\n".join(lines)
