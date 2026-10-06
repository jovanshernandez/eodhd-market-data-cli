"""Thin, retrying HTTP client for the EODHD REST API."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

import requests

from eodhd_market_data.models import Bar, Quote, SearchResult

DEFAULT_BASE_URL = "https://eodhd.com/api"
SEARCH_TYPES = ("all", "stock", "etf", "fund", "index", "crypto")
PERIODS = ("d", "w", "m")
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEMO_TICKERS = ("AAPL.US", "TSLA.US", "VTI.US", "AMZN.US", "BTC-USD.CC", "EURUSD.FOREX")


class EodhdError(RuntimeError):
    """Raised for any failure talking to EODHD. Messages never contain the API key."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class EodhdClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff: float = 0.5,
        max_backoff: float = 8.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: Callable[[int, float, str], None] | None = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self._session = session or requests.Session()
        self._sleep = sleep
        self._on_retry = on_retry

    def __repr__(self) -> str:  # keep the key out of logs and tracebacks
        return f"EodhdClient(base_url={self.base_url!r}, api_key='***')"

    # Public API ---------------------------------------------------------

    def search(self, query: str, asset_type: str = "all", exchange: str | None = None, limit: int = 15) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise ValueError("query is required")
        asset_type = asset_type.lower()
        if asset_type not in SEARCH_TYPES:
            raise ValueError(f"asset_type must be one of: {', '.join(SEARCH_TYPES)}")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        params: dict[str, Any] = {"type": asset_type, "limit": limit}
        if exchange:
            params["exchange"] = exchange
        payload = self._get(f"search/{query}", params)
        if not isinstance(payload, list):
            raise EodhdError("unexpected search response format")
        return [SearchResult.from_api(item) for item in payload]

    def eod(self, ticker: str, start: date | None = None, end: date | None = None, period: str = "d") -> list[Bar]:
        ticker = _normalize_ticker(ticker)
        if period not in PERIODS:
            raise ValueError(f"period must be one of: {', '.join(PERIODS)}")
        if start and end and start > end:
            raise ValueError("start date must be on or before end date")
        params: dict[str, Any] = {"period": period, "order": "a"}
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        payload = self._get(f"eod/{ticker}", params)
        if not isinstance(payload, list):
            raise EodhdError(f"unexpected EOD response format for {ticker}")
        return [Bar.from_api(item) for item in payload]

    def quotes(self, tickers: Iterable[str]) -> list[Quote]:
        symbols = [_normalize_ticker(t) for t in tickers]
        if not symbols:
            raise ValueError("at least one ticker is required")
        params: dict[str, Any] = {}
        if len(symbols) > 1:
            params["s"] = ",".join(symbols[1:])
        payload = self._get(f"real-time/{symbols[0]}", params)
        items = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in items):
            raise EodhdError("unexpected quote response format")
        return [Quote.from_api(item) for item in items]

    # Transport ----------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}/{path}"
        query = {**params, "fmt": "json", "api_token": self._api_key}
        attempt = 0
        while True:
            try:
                response = self._session.get(url, params=query, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise EodhdError(f"network error after {attempt + 1} attempts: {self._redact(exc)}") from None
                reason = type(exc).__name__
                delay = self._delay(attempt, None)
            else:
                status = response.status_code
                if status < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise EodhdError(f"EODHD returned non-JSON content for /{path}", status) from None
                if status not in RETRY_STATUSES or attempt >= self.max_retries:
                    raise self._http_error(status, path, response, attempt)
                reason = f"HTTP {status}"
                delay = self._delay(attempt, response.headers.get("Retry-After"))
            attempt += 1
            if self._on_retry:
                self._on_retry(attempt, delay, reason)
            self._sleep(delay)

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.max_backoff)
            except ValueError:
                pass
        base = min(self.backoff * (2**attempt), self.max_backoff)
        return base * random.uniform(0.5, 1.0)  # jitter so parallel callers don't sync up

    def _http_error(self, status: int, path: str, response: requests.Response, attempt: int) -> EodhdError:
        endpoint = "/" + path
        if status == 401:
            return EodhdError("EODHD rejected the API key (401). Check EODHD_API_KEY.", status)
        if status == 403:
            if self._api_key == "demo":
                hint = f"the demo key covers {', '.join(DEMO_TICKERS)} for prices and quotes, and no search"
            else:
                hint = "your EODHD plan may not include this endpoint, exchange or ticker"
            return EodhdError(f"access denied for {endpoint} (403): {hint}", status)
        if status == 404:
            return EodhdError(f"not found: {endpoint} (404). Check the ticker format, e.g. AAPL.US.", status)
        if status in RETRY_STATUSES:
            return EodhdError(f"EODHD returned HTTP {status} for {endpoint} after {attempt + 1} attempts", status)
        detail = self._redact(response.text.strip())[:200]
        return EodhdError(f"EODHD returned HTTP {status} for {endpoint}: {detail}", status)

    def _redact(self, value: object) -> str:
        return str(value).replace(self._api_key, "***")


def _normalize_ticker(ticker: str) -> str:
    ticker = ticker.strip().upper()
    if not ticker or "/" in ticker:
        raise ValueError(f"invalid ticker: {ticker!r}")
    return ticker
