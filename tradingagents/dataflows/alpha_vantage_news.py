import json

from .alpha_vantage_common import _make_api_request, format_datetime_for_api
from .utils import get_current_date


def get_news(ticker, start_date, end_date) -> dict[str, str] | str:
    """Returns live and historical market news & sentiment data from premier news outlets worldwide.

    Covers stocks, cryptocurrencies, forex, and topics like fiscal policy, mergers & acquisitions, IPOs.

    Args:
        ticker: Stock symbol for news articles.
        start_date: Start date for news search.
        end_date: End date for news search.

    Returns:
        Dictionary containing news sentiment data or JSON string.
    """

    params = {
        "tickers": ticker,
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(end_date),
    }

    return _make_api_request("NEWS_SENTIMENT", params)

def get_global_news(curr_date, look_back_days: int = 7, limit: int = 50) -> dict[str, str] | str:
    """Returns global market news & sentiment data without ticker-specific filtering.

    Covers broad market topics like financial markets, economy, and more.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Number of days to look back (default 7).
        limit: Maximum number of articles (default 50).

    Returns:
        Dictionary containing global news sentiment data or JSON string.
    """
    from datetime import datetime, timedelta

    # Calculate start date
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    params = {
        "topics": "financial_markets,economy_macro,economy_monetary",
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(curr_date),
        "limit": str(limit),
    }

    return _make_api_request("NEWS_SENTIMENT", params)


def get_insider_transactions(symbol: str, curr_date: str | None = None) -> dict[str, str] | str:
    """Returns latest and historical insider transactions by key stakeholders.

    Covers transactions by founders, executives, board members, etc.

    Args:
        symbol: Ticker symbol. Example: "IBM".

    Returns:
        Dictionary containing insider transaction data or JSON string.
    """

    if curr_date and curr_date < get_current_date():
        return (
            f"<unavailable: insider transactions for {symbol} as of {curr_date}: "
            "Alpha Vantage exposes transaction dates but not Form 4 filing dates, "
            "so their public availability by the analysis date cannot be verified>"
        )

    params = {
        "symbol": symbol,
    }

    response = _make_api_request("INSIDER_TRANSACTIONS", params)
    if not curr_date:
        return response
    try:
        payload = json.loads(response) if isinstance(response, str) else response
    except (TypeError, ValueError):
        return f"<unavailable: insider transactions for {symbol} as of {curr_date}: provider response has no usable dates>"
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return f"<unavailable: insider transactions for {symbol} as of {curr_date}: provider response has no usable dated rows>"
    dated = [row for row in payload["data"] if isinstance(row, dict) and isinstance(row.get("transaction_date"), str)]
    if not dated:
        if not payload["data"]:
            return json.dumps(payload)
        return f"<unavailable: insider transactions for {symbol} as of {curr_date}: provider supplied no dated coverage>"
    kept = [row for row in dated if row["transaction_date"] <= curr_date]
    if not kept:
        return f"<unavailable: insider transactions for {symbol} as of {curr_date}: provider supplied no covered historical rows>"
    payload["data"] = kept
    payload["point_in_time_notice"] = (
        "Rows use transaction dates, not Form 4 filing dates; the newest trades "
        "may not have been public on the analysis date."
    )
    return json.dumps(payload)
