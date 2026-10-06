"""Command line client for the EODHD market data API."""

__version__ = "0.2.0"

from eodhd_market_data.client import EodhdClient, EodhdError  # noqa: E402
from eodhd_market_data.models import Bar, Quote, SearchResult  # noqa: E402

__all__ = ["Bar", "EodhdClient", "EodhdError", "Quote", "SearchResult", "__version__"]
