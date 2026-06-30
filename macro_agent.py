"""
Macro Agent -- synthesizes broad market conditions using Claude.

Usage:
  python3 macro_agent.py                     # default tickers
  python3 macro_agent.py SPY QQQ BTC-USD     # custom tickers
  python3 macro_agent.py --email             # default tickers + email
  python3 macro_agent.py SPY QQQ --email     # custom tickers + email
"""

import os
import sys
import smtplib
import anthropic
from datetime import datetime
from email.message import EmailMessage
from fetch_stock_data import fetch_stock_data

DEFAULT_TICKERS = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq-100 ETF",
    "SPCX": "SpaceX",
    "TLT": "20yr Treasury Bond ETF",
    "GLD": "Gold ETF",
    "MU": "Micron Technology",
    "NVDA": "NVIDIA",
    "XOM": "Exxon Mobil",
}


def resolve_tickers(cli_args):
    if not cli_args:
        return DEFAULT_TICKERS
    return {t.upper(): t.upper() for t in cli_args}


def format_ticker_block(ticker, description, data):
    price = data["price"]
    fund = data["fundamentals"]
    news = data["news"]

    change_str = "N/A"
    if price["change"] is not None:
        arrow = "up" if price["change"] >= 0 else "down"
        change_str = arrow + " " + str(abs(price["change_pct"])) + "%"

    range_str = ""
    hi = fund.get("52w_high")
    lo = fund.get("52w_low")
    if hi and lo and price["current"] and hi != lo:
        pct_of_range = (price["current"] - lo) / (hi - lo) * 100
        range_str = " | 52w position: " + str(round(pct_of_range)) + "%"

    headline_str = ""
    if news:
        top = news[:2]
        headlines = "; ".join(a["title"] for a in top)
        headline_str = " | Headlines: " + headlines

    return ticker + " (" + description + "): $" + str(price["current"]) + " " + change_str + range_str + headline_str


def build_market_snapshot(tickers):
    lines = []
    for ticker, description in tickers.items():
        print("  Fetching " + ticker + " ...")
        try:
            data = fetch_stock_data(ticker)
            lines.append(format_ticker_block(ticker, description, data))
        except Exception as e:
            lines.append(ticker + ": Could not fetch -- " + str(e))
    return "\n".join(lines)


def get_report(snapshot, tickers):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        print("Export it first:  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    ticker_list = ", ".join(tickers.keys())
    prompt = (
        "You are a seasoned macro analyst. Here is today's live market data:\n\n"
        + snapshot
        + "\n\nWrite a structured macro sentiment report covering these tickers: "
        + ticker_list
        + ".\nUse the following sections (2-4 bullet points each):\n"
        "1. Overall Market Tone (risk-on / risk-off / mixed)\n"
        "2. Key Signals (what the price action and positioning tell us)\n"
        "3. Fear & Volatility (if VIX is present, interpret it; otherwise skip)\n"
        "4. Rates & Safe Havens (if TLT or GLD are present; otherwise skip)\n"
        "5. Key Risks & Watchpoints (2-3 items to monitor)\n\n"
        "Be concise. Use only what the data supports. Do not add footnotes or closing notes."
    )

    client = anthropic.Anthropic(api_key=api_key)
    report_parts = []

    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            report_parts.append(text)

    return "".join(report_parts)


def send_email(report, tickers):
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    # Remove non-breaking spaces that can sneak in from copy-paste
    gmail_password = gmail_password.replace("\xa0", " ").replace("\xc2\xa0", " ").strip()

    # Remove any non-ASCII characters
    clean_report = report.encode("ascii", errors="replace").decode("ascii")
    ticker_str = ", ".join(tickers.keys())
    date_str = datetime.now().strftime("%B %d, %Y")

    body = "Macro Market Report - " + date_str + "\nTickers: " + ticker_str + "\n\n" + clean_report

    msg = EmailMessage()
    msg["Subject"] = "Macro Market Report - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nReport emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


def run_macro_agent(tickers, send_to_email):
    print("\n" + "=" * 60)
    print("  MACRO AGENT  --  Broad Market Intelligence")
    print("  Tracking: " + ", ".join(tickers.keys()))
    print("=" * 60)
    print("\nGathering market data...\n")

    snapshot = build_market_snapshot(tickers)

    print("\nAsking Claude for macro analysis (streaming)...\n")
    print("=" * 60)
    print()

    report = get_report(snapshot, tickers)

    print("\n\n" + "=" * 60)
    print("  Macro report generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    if send_to_email:
        send_email(report, tickers)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--email"]
    send_to_email = "--email" in sys.argv[1:]
    tickers = resolve_tickers(args)
    run_macro_agent(tickers, send_to_email)
