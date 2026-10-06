"""`eodhd` command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eodhd_market_data import __version__
from eodhd_market_data.analytics import periods_per_year, simple_returns, summarize
from eodhd_market_data.client import PERIODS, SEARCH_TYPES, EodhdClient, EodhdError
from eodhd_market_data.models import Bar, Quote, SearchResult
from eodhd_market_data.output import (
    Style,
    fmt_int,
    fmt_pct,
    fmt_price,
    fmt_signed,
    records,
    summary_dict,
    summary_text,
    table,
    to_csv,
    to_json,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eodhd",
        description="Search instruments, pull end-of-day prices and quotes from the EODHD API.",
        epilog="The API key is read from EODHD_API_KEY. EODHD_API_KEY=demo works for AAPL.US, TSLA.US, "
        "VTI.US, AMZN.US, BTC-USD.CC and EURUSD.FOREX.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-key", help="EODHD API key (default: $EODHD_API_KEY). Prefer the env var.")
    common.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds (default: 10)")
    common.add_argument("--retries", type=int, default=3, help="retries on 429/5xx/network errors (default: 3)")
    common.add_argument("--format", choices=("table", "csv", "json"), help="output format (default: table)")
    common.add_argument("-o", "--output", type=Path, help="write CSV/JSON to this file (format from suffix)")
    common.add_argument("--color", choices=("auto", "always", "never"), default="auto")

    sub = parser.add_subparsers(dest="command", required=True, metavar="{search,prices,quote}")

    p = sub.add_parser("search", parents=[common], help="find instruments by name, ticker or ISIN")
    p.add_argument("query")
    p.add_argument("--type", dest="asset_type", default="all", choices=SEARCH_TYPES)
    p.add_argument("--exchange", help="limit to one exchange code, e.g. US, LSE, XETRA, CC, FOREX")
    p.add_argument("--limit", type=int, default=15)

    p = sub.add_parser("prices", parents=[common], help="end-of-day OHLCV history for one ticker")
    p.add_argument("ticker", help="TICKER.EXCHANGE, e.g. AAPL.US, BTC-USD.CC, EURUSD.FOREX")
    p.add_argument("--from", dest="start", type=_iso_date, help="start date YYYY-MM-DD (default: --days before --to)")
    p.add_argument("--to", dest="end", type=_iso_date, help="end date YYYY-MM-DD (default: today)")
    p.add_argument("--days", type=int, default=30, help="calendar days of history when --from is omitted (default: 30)")
    p.add_argument("--period", choices=PERIODS, default="d", help="bar size: d(aily), w(eekly), m(onthly)")
    p.add_argument("--summary", action="store_true", help="print a simple-returns summary")

    p = sub.add_parser("quote", parents=[common], help="latest (delayed) quote for one or more tickers")
    p.add_argument("tickers", nargs="+", metavar="ticker")
    return parser


# Renderers ----------------------------------------------------------------


def _search_table(results: list[SearchResult], style: Style) -> str:
    rows = [
        [r.ticker, r.name[:40], r.type or "", r.country or "", r.currency or "", r.isin or "", fmt_price(r.previous_close)]
        for r in results
    ]
    return table(["Ticker", "Name", "Type", "Country", "Ccy", "ISIN", "Prev close"], rows, "llllllr", style)


def _prices_table(bars: list[Bar], style: Style) -> str:
    changes = {d: r for d, r in simple_returns(bars)}
    rows = []
    for b in bars:
        r = changes.get(b.date)
        rows.append([
            b.date.isoformat(), fmt_price(b.open), fmt_price(b.high), fmt_price(b.low), fmt_price(b.close),
            fmt_price(b.adjusted_close), fmt_int(b.volume), style.signed(r, fmt_pct(r)) if r is not None else "",
        ])
    return table(["Date", "Open", "High", "Low", "Close", "Adj close", "Volume", "Return"], rows, "lrrrrrrr", style)


def _quote_table(quotes: list[Quote], style: Style) -> str:
    rows = []
    for q in quotes:
        pct = q.change_pct / 100 if q.change_pct is not None else None
        rows.append([
            q.ticker, fmt_price(q.close), style.signed(q.change, fmt_signed(q.change, q.close)),
            style.signed(pct, fmt_pct(pct)), fmt_price(q.open), fmt_price(q.high), fmt_price(q.low),
            fmt_price(q.previous_close), fmt_int(q.volume),
            q.timestamp.strftime("%Y-%m-%d %H:%M") if q.timestamp else "-",
        ])
    headers = ["Ticker", "Last", "Change", "Change %", "Open", "High", "Low", "Prev close", "Volume", "As of (UTC)"]
    return table(headers, rows, "lrrrrrrrrl", style)


# Plumbing -----------------------------------------------------------------


def _resolve_format(args: argparse.Namespace) -> str:
    if args.output is not None:
        if args.format in ("csv", "json"):
            return args.format
        return "json" if args.output.suffix.lower() == ".json" else "csv"
    return args.format or "table"


def _emit(args: argparse.Namespace, fmt: str, rows: Any, rendered_table: str) -> None:
    if fmt == "table":
        print(rendered_table)
        return
    text = to_json(rows) if fmt == "json" else to_csv(rows)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        count = len(rows["bars"]) if isinstance(rows, dict) else len(rows)
        print(f"wrote {count} rows to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def _use_color(choice: str, stream: Any) -> bool:
    if choice != "auto":
        return choice == "always"
    return "NO_COLOR" not in os.environ and hasattr(stream, "isatty") and stream.isatty()


def _make_client(args: argparse.Namespace) -> EodhdClient:
    def on_retry(attempt: int, delay: float, reason: str) -> None:
        print(f"eodhd: {reason}, retry {attempt}/{args.retries} in {delay:.1f}s", file=sys.stderr)

    return EodhdClient(args.api_key, timeout=args.timeout, max_retries=args.retries, on_retry=on_retry)


def run(args: argparse.Namespace, client: EodhdClient) -> int:
    fmt = _resolve_format(args)
    style = Style(_use_color(args.color, sys.stdout))

    if args.command == "search":
        results = client.search(args.query, args.asset_type, args.exchange, args.limit)
        if not results:
            print(f"no instruments match {args.query!r}", file=sys.stderr)
            return 1
        _emit(args, fmt, records(results), _search_table(results, style) if fmt == "table" else "")
        return 0

    if args.command == "prices":
        end = args.end or date.today()
        start = args.start or end - timedelta(days=args.days)
        ticker = args.ticker.upper()
        bars = client.eod(ticker, start, end, args.period)
        if not bars:
            print(f"no bars for {ticker} between {start} and {end}", file=sys.stderr)
            return 1
        if not args.summary or len(bars) < 2:
            if args.summary:
                print("summary needs at least two bars", file=sys.stderr)
            _emit(args, fmt, records(bars), _prices_table(bars, style) if fmt == "table" else "")
            return 0
        ann = periods_per_year(ticker, args.period)
        summary = summarize(bars, ann)
        if fmt == "json":  # one document: {"ticker", "bars", "summary"}
            doc = {"ticker": ticker, "bars": records(bars), "summary": summary_dict(ticker, summary)}
            _emit(args, fmt, doc, "")
            return 0
        _emit(args, fmt, records(bars), _prices_table(bars, style) if fmt == "table" else "")
        if fmt == "table" or args.output is not None:
            print(("\n" if fmt == "table" else "") + summary_text(ticker, summary, ann, style))
        else:  # CSV on stdout: keep the stream clean, summary goes to stderr
            print(summary_text(ticker, summary, ann, Style(_use_color(args.color, sys.stderr))), file=sys.stderr)
        return 0

    quotes = client.quotes(args.tickers)
    _emit(args, fmt, records(quotes), _quote_table(quotes, style) if fmt == "table" else "")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.api_key = args.api_key or os.environ.get("EODHD_API_KEY", "").strip()
    if not args.api_key:
        parser.error("set EODHD_API_KEY (try EODHD_API_KEY=demo) or pass --api-key")
    if args.output is not None and args.format == "table":
        parser.error("--output writes csv or json; drop --format table")
    try:
        return run(args, _make_client(args))
    except (EodhdError, ValueError) as exc:
        print(f"eodhd: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
