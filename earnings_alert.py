"""
Earnings Alert -- tracks upcoming earnings for watchlist stocks.

For every stock in your watchlist, checks if earnings are within 7 days.
Sends a heads-up email listing all upcoming reports, and automatically
runs the Equity Analyst on any stock you OWN that is reporting soon.

Usage:
  python3 earnings_alert.py           # print to terminal
  python3 earnings_alert.py --email   # print + email
"""

import os
import sys
import smtplib
import anthropic
import yfinance as yf
from datetime import datetime, timedelta
from email.message import EmailMessage
from fetch_stock_data import fetch_stock_data

# ── Configuration ─────────────────────────────────────────────────────────────

# Stocks to monitor for earnings -- add any ticker you want to track
WATCHLIST = [
    "SPCX", "NVDA", "AAPL", "MSFT", "AMZN",
    "GOOGL", "META", "TSLA", "NFLX", "AMD",
]

# Stocks you actually own -- these get an automatic deep-dive if reporting soon
OWNED = ["SPCX", "NVDA", "PLTR"]

# Alert window -- warn if earnings are within this many days
ALERT_DAYS = 7


# ── Earnings Check ────────────────────────────────────────────────────────────

def get_earnings_date(ticker: str):
    """Return the next earnings date for a ticker, or None if not found."""
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None:
            return None
        # calendar can be a dict or DataFrame depending on yfinance version
        if isinstance(cal, dict):
            date = cal.get("Earnings Date")
            if isinstance(date, list) and date:
                return date[0]
            return date
        # DataFrame format
        if hasattr(cal, "loc"):
            try:
                date = cal.loc["Earnings Date"].iloc[0]
                return date
            except Exception:
                return None
    except Exception:
        return None


def check_watchlist() -> list:
    """Check all watchlist tickers and return those with earnings within ALERT_DAYS."""
    upcoming = []
    today = datetime.now().date()
    cutoff = today + timedelta(days=ALERT_DAYS)

    print("Checking earnings dates...\n")
    for ticker in WATCHLIST:
        earnings_date = get_earnings_date(ticker)
        if earnings_date is None:
            print("  " + ticker + " -- no earnings date found")
            continue

        # Normalize to date object -- handle list, datetime, or date
        if isinstance(earnings_date, list):
            if not earnings_date:
                continue
            earnings_date = earnings_date[0]
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        if not hasattr(earnings_date, "year"):
            continue

        days_away = (earnings_date - today).days
        if 0 <= days_away <= ALERT_DAYS:
            owned = ticker in OWNED
            upcoming.append({
                "ticker": ticker,
                "earnings_date": earnings_date,
                "days_away": days_away,
                "owned": owned,
            })
            tag = " [YOU OWN THIS]" if owned else ""
            print("  " + ticker + " -- reports " + str(earnings_date) + " (" + str(days_away) + " days)" + tag)
        else:
            print("  " + ticker + " -- next earnings: " + str(earnings_date))

    return upcoming


# ── Equity Deep-Dive for Owned Stocks ────────────────────────────────────────

def run_equity_deepdive(ticker: str) -> str:
    """Run the full equity analyst on a ticker and return the report."""
    print("\nRunning deep-dive on " + ticker + " (earnings coming up)...\n")

    # Import SEC EDGAR helpers from equity_agent
    sys.path.insert(0, os.path.dirname(__file__))
    from equity_agent import get_cik, get_latest_10k_text, build_equity_prompt, get_analysis

    try:
        stock_data = fetch_stock_data(ticker)
    except Exception as e:
        return "Could not fetch market data for " + ticker + ": " + str(e)

    try:
        cik = get_cik(ticker)
        ten_k_text = get_latest_10k_text(cik)
    except Exception:
        ten_k_text = "10-K not available."

    prompt = build_equity_prompt(ticker, stock_data, ten_k_text)
    print("=" * 60 + "\n")
    report = get_analysis(prompt)
    print("\n\n" + "=" * 60)
    return report


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(upcoming: list, deepdives: dict):
    """Email the earnings alert and any deep-dives."""
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    date_str = datetime.now().strftime("%B %d, %Y")

    if not upcoming:
        body = "Earnings Alert - " + date_str + "\n\nNo earnings in the next " + str(ALERT_DAYS) + " days for your watchlist."
    else:
        lines = ["Earnings Alert - " + date_str, ""]
        lines.append("Upcoming earnings in the next " + str(ALERT_DAYS) + " days:\n")
        for item in upcoming:
            tag = " ** YOU OWN THIS **" if item["owned"] else ""
            lines.append(
                "  " + item["ticker"] + " -- " + str(item["earnings_date"])
                + " (" + str(item["days_away"]) + " days away)" + tag
            )

        if deepdives:
            lines.append("\n\n" + "=" * 50)
            lines.append("AUTO DEEP-DIVE REPORTS (stocks you own)\n")
            for ticker, report in deepdives.items():
                clean = report.encode("ascii", errors="replace").decode("ascii")
                lines.append("--- " + ticker + " ---\n")
                lines.append(clean)
                lines.append("")

        lines.append("\n---\nGenerated by Earnings Alert - claude-opus-4-8")
        body = "\n".join(lines)

    subject = "Earnings Alert - " + date_str
    if upcoming:
        tickers = ", ".join(i["ticker"] for i in upcoming)
        subject = "Earnings Alert: " + tickers + " reporting soon"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nEarnings alert emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    send_to_email = "--email" in sys.argv[1:]

    print("\n" + "=" * 60)
    print("  EARNINGS ALERT -- " + str(ALERT_DAYS) + "-Day Watchlist")
    print("=" * 60 + "\n")

    upcoming = check_watchlist()

    if not upcoming:
        print("\nNo earnings in the next " + str(ALERT_DAYS) + " days for your watchlist.")
        if send_to_email:
            send_email([], {})
        return

    print("\nUpcoming earnings:\n")
    for item in upcoming:
        tag = " [YOU OWN THIS]" if item["owned"] else ""
        print("  " + item["ticker"] + " -- " + str(item["earnings_date"]) + " (" + str(item["days_away"]) + " days)" + tag)

    # Run deep-dives on owned stocks reporting soon
    deepdives = {}
    owned_upcoming = [i for i in upcoming if i["owned"]]
    if owned_upcoming:
        print("\nRunning automatic deep-dives on stocks you own...")
        for item in owned_upcoming:
            deepdives[item["ticker"]] = run_equity_deepdive(item["ticker"])

    if send_to_email:
        send_email(upcoming, deepdives)


if __name__ == "__main__":
    run()
