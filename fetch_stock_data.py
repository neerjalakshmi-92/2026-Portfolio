"""
Stock Data Fetcher — Foundation tool for the Investment Agents system.

Fetches current price, key stats, and recent news headlines for any ticker.
Used by both the Macro Agent (broad market tickers like SPY, QQQ) and the
Equity Analyst Agent (individual stocks like AAPL, NVDA).
"""

import yfinance as yf
from datetime import datetime


def fetch_stock_data(ticker: str) -> dict:
    """
    Pull current price, fundamentals, and recent news for a given ticker.

    Args:
        ticker: Stock symbol, e.g. "AAPL" or "SPY"

    Returns:
        A dictionary with price info, key stats, and news headlines.
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    # --- Price & Basic Info ---
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    previous_close = info.get("previousClose") or info.get("regularMarketPreviousClose")

    price_change = None
    price_change_pct = None
    if current_price and previous_close:
        price_change = round(current_price - previous_close, 2)
        price_change_pct = round((price_change / previous_close) * 100, 2)

    # --- Key Fundamentals ---
    fundamentals = {
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "eps": info.get("trailingEps"),
        "dividend_yield": info.get("dividendYield"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "avg_volume": info.get("averageVolume"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }

    # --- Recent News (up to 5 headlines) ---
    raw_news = stock.news or []
    news_headlines = []
    for article in raw_news[:5]:
        content = article.get("content", {})
        title = content.get("title") or article.get("title", "No title")
        provider = content.get("provider", {}).get("displayName") or article.get("publisher", "Unknown")
        pub_date = content.get("pubDate") or ""
        # Format date if present (e.g. "2026-06-17T14:30:00Z" → "2026-06-17")
        short_date = pub_date[:10] if pub_date else "N/A"
        news_headlines.append({
            "title": title,
            "source": provider,
            "date": short_date,
        })

    return {
        "ticker": ticker.upper(),
        "name": info.get("longName", ticker.upper()),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price": {
            "current": current_price,
            "previous_close": previous_close,
            "change": price_change,
            "change_pct": price_change_pct,
        },
        "fundamentals": fundamentals,
        "news": news_headlines,
    }


def print_report(data: dict):
    """Pretty-print the fetched stock data to the terminal."""

    ticker = data["ticker"]
    name = data["name"]
    price = data["price"]
    fund = data["fundamentals"]
    news = data["news"]

    # Direction arrow for price change
    if price["change"] is not None:
        arrow = "▲" if price["change"] >= 0 else "▼"
        change_str = f"{arrow} {abs(price['change']):.2f} ({abs(price['change_pct']):.2f}%)"
    else:
        change_str = "N/A"

    print("\n" + "=" * 55)
    print(f"  {ticker}  —  {name}")
    print("=" * 55)

    # Price block
    print(f"\n  {'Current Price:':<22} ${price['current']}")
    print(f"  {'Previous Close:':<22} ${price['previous_close']}")
    print(f"  {'Change:':<22} {change_str}")

    # Fundamentals block
    print(f"\n  — Fundamentals —")
    if fund["sector"]:
        print(f"  {'Sector / Industry:':<22} {fund['sector']} / {fund['industry']}")
    if fund["market_cap"]:
        cap_b = fund["market_cap"] / 1_000_000_000
        print(f"  {'Market Cap:':<22} ${cap_b:.1f}B")
    if fund["pe_ratio"]:
        print(f"  {'P/E (trailing):':<22} {fund['pe_ratio']:.1f}")
    if fund["forward_pe"]:
        print(f"  {'P/E (forward):':<22} {fund['forward_pe']:.1f}")
    if fund["eps"]:
        print(f"  {'EPS (trailing):':<22} ${fund['eps']:.2f}")
    if fund["dividend_yield"]:
        print(f"  {'Dividend Yield:':<22} {fund['dividend_yield']*100:.2f}%")
    if fund["52w_high"] and fund["52w_low"]:
        print(f"  {'52-Week Range:':<22} ${fund['52w_low']} – ${fund['52w_high']}")

    # News block
    print(f"\n  — Recent News —")
    if news:
        for i, article in enumerate(news, 1):
            print(f"\n  {i}. [{article['date']}] {article['source']}")
            print(f"     {article['title']}")
    else:
        print("  No recent news found.")

    print("\n" + "=" * 55)
    print(f"  Fetched at: {data['fetched_at']}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    import sys

    # Accept a ticker from the command line, default to AAPL for easy testing
    ticker_input = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

    print(f"\nFetching data for: {ticker_input.upper()} ...")
    result = fetch_stock_data(ticker_input)
    print_report(result)
