"""
Stock Recommender -- twice-weekly email with 6 stocks worth learning about.

Pulls recent news from major market ETFs and sectors, then asks Claude
to recommend 6 stocks based on what is moving in the market that day.
Remembers recently recommended stocks so it doesn't repeat them.

Usage:
  python3 stock_recommender.py           # print to terminal
  python3 stock_recommender.py --email   # print + email
"""

import os
import sys
import re
import json
import smtplib
import anthropic
from datetime import datetime, timedelta
from email.message import EmailMessage
from fetch_stock_data import fetch_stock_data

# Broad set of tickers to pull news from -- covers major indices and sectors
NEWS_SOURCES = [
    "SPY", "QQQ", "IWM",   # broad market
    "XLK", "XLF", "XLE",   # tech, financials, energy
    "XLV", "XLY", "XLI",   # health, consumer, industrials
    "GLD", "TLT", "VIX",   # macro signals
]

# How many stocks to feature each run
NUM_STOCKS = 6

# Don't repeat a stock recommended within this many days
REPEAT_WINDOW_DAYS = 30

# File that remembers which stocks were recently recommended (committed back to the repo)
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "watchlist_history.json")


# ── History (avoid repeating stocks across runs) ──────────────────────────────

def load_recent_tickers() -> set:
    """Return the set of tickers recommended within the last REPEAT_WINDOW_DAYS."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    try:
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
    except Exception:
        return set()

    cutoff = datetime.now() - timedelta(days=REPEAT_WINDOW_DAYS)
    recent = set()
    for entry in history:
        try:
            if datetime.strptime(entry["date"], "%Y-%m-%d") >= cutoff:
                recent.add(entry["ticker"])
        except Exception:
            continue
    return recent


def record_recommended(tickers: list):
    """Append today's tickers to history and prune old entries."""
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            history = []

    today = datetime.now().strftime("%Y-%m-%d")
    for t in tickers:
        history.append({"ticker": t, "date": today})

    cutoff = datetime.now() - timedelta(days=REPEAT_WINDOW_DAYS)
    pruned = [e for e in history if _safe_recent(e, cutoff)]

    with open(HISTORY_FILE, "w") as f:
        json.dump(pruned, f, indent=2)
    print("Recorded " + str(len(tickers)) + " recommended tickers to history.")


def _safe_recent(entry, cutoff) -> bool:
    try:
        return datetime.strptime(entry["date"], "%Y-%m-%d") >= cutoff
    except Exception:
        return False


def parse_tickers(report: str) -> list:
    """Extract tickers from the 'TICKERS:' line Claude adds at the top."""
    for line in report.splitlines():
        if line.strip().upper().startswith("TICKERS:"):
            raw = line.split(":", 1)[1]
            return [t.strip().upper() for t in raw.split(",") if t.strip()]
    return []


def gather_news() -> str:
    """Pull headlines from across the market and format for Claude."""
    print("Gathering market news...\n")
    all_headlines = []

    for ticker in NEWS_SOURCES:
        try:
            data = fetch_stock_data(ticker)
            price = data["price"]
            change_str = "flat"
            if price["change_pct"] is not None:
                direction = "up" if price["change_pct"] >= 0 else "down"
                change_str = direction + " " + str(abs(price["change_pct"])) + "%"

            for article in data["news"][:2]:
                all_headlines.append(
                    "[" + ticker + " " + change_str + "] " + article["title"] + " (" + article["source"] + ", " + article["date"] + ")"
                )
            print("  " + ticker + " -- " + str(len(data["news"])) + " headlines collected")
        except Exception as e:
            print("  " + ticker + " -- skipped: " + str(e))

    return "\n".join(all_headlines)


def get_recommendations(headlines: str, recent: set) -> str:
    """Ask Claude to recommend NUM_STOCKS stocks based on the news, avoiding repeats."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    today = datetime.now().strftime("%B %d, %Y")

    avoid_str = ""
    if recent:
        avoid_str = (
            "\n\nIMPORTANT: Do NOT recommend any of these tickers -- they were featured recently "
            "and the investor is learning new names:\n" + ", ".join(sorted(recent)) + "\n"
        )

    prompt = (
        "You are a stock market educator helping a beginner investor learn about the market.\n\n"
        "Today is " + today + ". Here are today's market headlines:\n\n"
        + headlines
        + avoid_str
        + "\n\nBased on these headlines and current market conditions, recommend exactly "
        + str(NUM_STOCKS) + " stocks worth learning about today. Aim for variety across sectors "
        "and mix well-known names with a few lesser-known ones.\n\n"
        "FIRST, output a single line in EXACTLY this format (used by software, so be precise):\n"
        "TICKERS: SYM1, SYM2, SYM3, SYM4, SYM5, SYM6\n\n"
        "THEN, for each of the " + str(NUM_STOCKS) + " stocks include:\n"
        "- Ticker and company name\n"
        "- Sector\n"
        "- Why it is relevant today (1-2 sentences tied to the news)\n"
        "- One key thing a beginner should understand about this company\n"
        "- Risk level: Low / Medium / High\n\n"
        "Number them 1-" + str(NUM_STOCKS) + ". Do not add any closing notes or disclaimers after the last stock."
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


def send_email(report: str):
    """Email the recommendations via Gmail."""
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    # Strip the machine-readable TICKERS line from the emailed version
    visible = "\n".join(
        line for line in report.splitlines()
        if not line.strip().upper().startswith("TICKERS:")
    ).strip()
    clean_report = visible.encode("ascii", errors="replace").decode("ascii")
    date_str = datetime.now().strftime("%B %d, %Y")

    body = (
        "Your Stock Watchlist - " + date_str + "\n\n"
        + str(NUM_STOCKS) + " fresh stocks worth learning about, picked from market news "
        "(no repeats from the last " + str(REPEAT_WINDOW_DAYS) + " days).\n\n"
        + clean_report
        + "\n\n---\nGenerated by Stock Recommender - claude-opus-4-8"
    )

    msg = EmailMessage()
    msg["Subject"] = "Stock Watchlist - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nWatchlist emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


def run():
    send_to_email = "--email" in sys.argv[1:]

    print("\n" + "=" * 60)
    print("  STOCK RECOMMENDER -- Daily Watchlist")
    print("=" * 60 + "\n")

    headlines = gather_news()

    recent = load_recent_tickers()
    print("\n  " + str(len(recent)) + " tickers excluded as recently recommended")

    print("\nAsking Claude for " + str(NUM_STOCKS) + " fresh stocks...\n")
    print("=" * 60 + "\n")

    report = get_recommendations(headlines, recent)

    print("\n\n" + "=" * 60)
    print("  Recommendations generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    # Remember the tickers so they aren't repeated next run
    tickers = parse_tickers(report)
    if tickers:
        record_recommended(tickers)
    else:
        print("Warning: could not parse tickers from report -- history not updated.")

    if send_to_email:
        send_email(report)


if __name__ == "__main__":
    run()
