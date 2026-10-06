from __future__ import annotations

from datetime import date

import pytest
import requests

from conftest import BARS, FakeResponse, FakeSession
from eodhd_market_data.client import EodhdClient, EodhdError


def make_client(session: FakeSession, **kwargs) -> tuple[EodhdClient, list[float]]:
    sleeps: list[float] = []
    client = EodhdClient("secret-key", session=session, sleep=sleeps.append, **kwargs)
    return client, sleeps


def test_search_builds_request_and_parses_results() -> None:
    session = FakeSession(FakeResponse(payload=[
        {"Code": "AAPL", "Exchange": "US", "Name": "Apple Inc", "Type": "Common Stock", "Country": "USA",
         "Currency": "USD", "ISIN": "US0378331005", "previousClose": 333.69},
    ]))
    client, _ = make_client(session)

    results = client.search("apple", "stock", exchange="US", limit=5)

    assert results[0].ticker == "AAPL.US"
    assert results[0].isin == "US0378331005"
    assert results[0].previous_close == pytest.approx(333.69)
    call = session.calls[0]
    assert call["url"] == "https://eodhd.com/api/search/apple"
    assert call["params"] == {"type": "stock", "limit": 5, "exchange": "US", "fmt": "json", "api_token": "secret-key"}
    assert call["timeout"] == 10.0


def test_search_rejects_out_of_range_limit() -> None:
    client, _ = make_client(FakeSession())
    with pytest.raises(ValueError, match="limit"):
        client.search("apple", limit=0)


@pytest.mark.parametrize("asset_type", ["currency", "bonds"])
def test_search_rejects_unknown_asset_type(asset_type: str) -> None:
    client, _ = make_client(FakeSession())
    with pytest.raises(ValueError, match="asset_type must be one of"):
        client.search("eur", asset_type)


def test_eod_sends_date_range_and_parses_bars() -> None:
    session = FakeSession(FakeResponse(payload=BARS))
    client, _ = make_client(session)

    bars = client.eod("aapl.us", date(2026, 9, 1), date(2026, 9, 4), period="w")

    assert len(bars) == 4 and bars[1].close == 110.0 and bars[1].date == date(2026, 9, 2)
    call = session.calls[0]
    assert call["url"].endswith("/eod/AAPL.US")
    assert call["params"]["from"] == "2026-09-01" and call["params"]["to"] == "2026-09-04"
    assert call["params"]["period"] == "w"


def test_eod_rejects_inverted_range() -> None:
    client, _ = make_client(FakeSession())
    with pytest.raises(ValueError, match="start date"):
        client.eod("AAPL.US", date(2026, 9, 5), date(2026, 9, 1))


def test_quotes_batches_extra_tickers_into_s_param() -> None:
    payload = [
        {"code": "AAPL.US", "timestamp": 1791232020, "close": 332.89, "previousClose": 333.69, "change": -0.8, "change_p": -0.2397, "volume": 10},
        {"code": "EURUSD.FOREX", "timestamp": 1791256080, "close": 1.1217, "change": -0.0037, "change_p": -0.331, "volume": 0},
    ]
    session = FakeSession(FakeResponse(payload=payload))
    client, _ = make_client(session)

    quotes = client.quotes(["AAPL.US", "eurusd.forex"])

    assert [q.ticker for q in quotes] == ["AAPL.US", "EURUSD.FOREX"]
    assert quotes[0].change_pct == pytest.approx(-0.2397)
    assert session.calls[0]["url"].endswith("/real-time/AAPL.US")
    assert session.calls[0]["params"]["s"] == "EURUSD.FOREX"


def test_single_quote_object_is_wrapped_in_list() -> None:
    session = FakeSession(FakeResponse(payload={"code": "TSLA.US", "close": 378.73}))
    client, _ = make_client(session)
    assert client.quotes(["TSLA.US"])[0].close == pytest.approx(378.73)
    assert "s" not in session.calls[0]["params"]


# Retries -----------------------------------------------------------------


def test_retries_429_honoring_retry_after_then_succeeds() -> None:
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(payload=BARS),
    )
    retries: list[tuple[int, float, str]] = []
    client, sleeps = make_client(session, on_retry=lambda *a: retries.append(a))

    assert len(client.eod("AAPL.US")) == 4
    assert sleeps == [2.0]
    assert retries == [(1, 2.0, "HTTP 429")]
    assert len(session.calls) == 2


def test_retries_5xx_with_exponential_backoff_until_exhausted() -> None:
    session = FakeSession(*[FakeResponse(503) for _ in range(4)])
    client, sleeps = make_client(session, max_retries=3, backoff=1.0, max_backoff=8.0)

    with pytest.raises(EodhdError, match="HTTP 503 .* after 4 attempts") as err:
        client.eod("AAPL.US")

    assert err.value.status == 503
    assert len(session.calls) == 4
    # full backoff is 1, 2, 4 seconds; jitter keeps each delay within [50%, 100%] of it
    for delay, cap in zip(sleeps, [1.0, 2.0, 4.0]):
        assert cap / 2 <= delay <= cap


def test_backoff_is_capped() -> None:
    session = FakeSession(*[FakeResponse(500) for _ in range(6)])
    client, sleeps = make_client(session, max_retries=5, backoff=1.0, max_backoff=3.0)
    with pytest.raises(EodhdError):
        client.eod("AAPL.US")
    assert max(sleeps) <= 3.0


def test_retries_network_errors_and_timeouts() -> None:
    session = FakeSession(
        requests.ConnectionError("connection reset"),
        requests.Timeout("read timed out"),
        FakeResponse(payload=BARS),
    )
    client, sleeps = make_client(session)
    assert len(client.eod("AAPL.US")) == 4
    assert len(sleeps) == 2


@pytest.mark.parametrize("status, message", [
    (401, "rejected the API key"),
    (403, "access denied"),
    (404, "not found"),
    (422, "HTTP 422"),
])
def test_client_errors_fail_fast_without_retry(status: int, message: str) -> None:
    session = FakeSession(FakeResponse(status, text="bad request"))
    client, sleeps = make_client(session)
    with pytest.raises(EodhdError, match=message):
        client.eod("AAPL.US")
    assert sleeps == [] and len(session.calls) == 1


def test_demo_key_403_explains_demo_coverage() -> None:
    session = FakeSession(FakeResponse(403))
    client = EodhdClient("demo", session=session)
    with pytest.raises(EodhdError, match="demo key covers AAPL.US"):
        client.search("apple")


def test_api_key_never_appears_in_errors_or_repr() -> None:
    url = "https://eodhd.com/api/eod/AAPL.US?api_token=secret-key"
    session = FakeSession(*[requests.ConnectionError(f"Max retries exceeded with url: {url}") for _ in range(2)])
    client, _ = make_client(session, max_retries=1)

    with pytest.raises(EodhdError) as err:
        client.eod("AAPL.US")

    assert "secret-key" not in str(err.value)
    assert "api_token=***" in str(err.value)
    assert "secret-key" not in repr(client)


def test_non_json_body_is_a_clear_error() -> None:
    client, _ = make_client(FakeSession(FakeResponse(200, payload=None, text="<html>")))
    with pytest.raises(EodhdError, match="non-JSON"):
        client.eod("AAPL.US")


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        EodhdClient("")
