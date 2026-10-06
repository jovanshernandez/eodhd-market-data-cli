from __future__ import annotations

from typing import Any

import pytest
import requests


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Stands in for requests.Session: replays queued responses/exceptions and records calls."""

    def __init__(self, *responses: FakeResponse | Exception):
        self.queue = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tests must not touch the network")

    monkeypatch.setattr(requests.Session, "request", blocked)


BARS = [
    {"date": "2026-09-01", "open": 100, "high": 101, "low": 99, "close": 100, "adjusted_close": 100, "volume": 1000},
    {"date": "2026-09-02", "open": 100, "high": 111, "low": 100, "close": 110, "adjusted_close": 110, "volume": 2000},
    {"date": "2026-09-03", "open": 110, "high": 110, "low": 98, "close": 99, "adjusted_close": 99, "volume": 1500},
    {"date": "2026-09-04", "open": 99, "high": 121, "low": 99, "close": 120, "adjusted_close": 120, "volume": 1800},
]
