"""
Undervalued Stock Screener -- finds potentially undervalued stocks.

Scans the S&P 500 + Nasdaq 100, scores each stock on three lenses:
  1. Classic value (P/E, P/B, P/S, PEG vs the pack)
  2. Dividend & cash flow (dividend yield, free cash flow, low debt)
  3. Growth at a reasonable price (growth vs PEG)
Then asks Claude to write a beginner-friendly report on the top finds.

Usage:
  python3 undervalued_screener.py           # print to terminal
  python3 undervalued_screener.py --email   # print + email
"""

import os
import sys
import json
import smtplib
import requests
import anthropic
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from email.message import EmailMessage

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# How many stocks to feature each run
TOP_N = 6

# Don't repeat a stock that was featured within this many days
REPEAT_WINDOW_DAYS = 30

# File that remembers which stocks were recently featured (committed back to the repo)
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "screened_history.json")


# ── History (avoid repeating stocks across runs) ──────────────────────────────

def load_recent_tickers() -> set:
    """Return the set of tickers featured within the last REPEAT_WINDOW_DAYS."""
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
            seen_date = datetime.strptime(entry["date"], "%Y-%m-%d")
            if seen_date >= cutoff:
                recent.add(entry["ticker"])
        except Exception:
            continue
    return recent


def record_featured(tickers: list):
    """Append today's featured tickers to history and prune old entries."""
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

    # Prune anything older than the repeat window so stocks become eligible again
    cutoff = datetime.now() - timedelta(days=REPEAT_WINDOW_DAYS)
    pruned = []
    for entry in history:
        try:
            if datetime.strptime(entry["date"], "%Y-%m-%d") >= cutoff:
                pruned.append(entry)
        except Exception:
            continue

    with open(HISTORY_FILE, "w") as f:
        json.dump(pruned, f, indent=2)
    print("Recorded " + str(len(tickers)) + " featured tickers to history.")


# ── Build the universe ────────────────────────────────────────────────────────

def _read_wiki_tables(url: str):
    """Fetch a Wikipedia page with a browser header and parse its tables."""
    from io import StringIO
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def get_universe() -> list:
    """Fetch S&P 500 + Nasdaq 100 tickers from Wikipedia, deduplicated."""
    tickers = set()

    try:
        sp500 = _read_wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers.update(sp500["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist())
        print("  S&P 500: " + str(len(sp500)) + " tickers")
    except Exception as e:
        print("  Could not fetch S&P 500 list: " + str(e))

    try:
        tables = _read_wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
        for table in tables:
            if "Ticker" in table.columns:
                tickers.update(table["Ticker"].astype(str).str.replace(".", "-", regex=False).tolist())
                print("  Nasdaq 100: " + str(len(table)) + " tickers")
                break
    except Exception as e:
        print("  Could not fetch Nasdaq 100 list: " + str(e))

    # Clean out junk values
    clean = sorted(t for t in tickers if t and t != "nan" and len(t) <= 6)
    return clean


# ── Score each stock ──────────────────────────────────────────────────────────

def score_stock(info: dict) -> tuple:
    """
    Return (score, reasons) for a stock. Higher score = more undervalued
    signals. Reasons is a list of human-readable flags.
    """
    score = 0
    reasons = []

    pe = info.get("trailingPE")
    fwd_pe = info.get("forwardPE")
    pb = info.get("priceToBook")
    ps = info.get("priceToSalesTrailing12Months")
    peg = info.get("pegRatio")
    div = info.get("dividendYield")
    fcf = info.get("freeCashflow")
    mcap = info.get("marketCap")
    d2e = info.get("debtToEquity")
    rev_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    target = info.get("targetMeanPrice")
    price = info.get("currentPrice") or info.get("regularMarketPrice")

    # --- 1. Classic value ---
    if pe and 0 < pe < 15:
        score += 2; reasons.append("Low P/E (" + str(round(pe, 1)) + ")")
    if fwd_pe and 0 < fwd_pe < 13:
        score += 1; reasons.append("Low forward P/E (" + str(round(fwd_pe, 1)) + ")")
    if pb and 0 < pb < 1.5:
        score += 2; reasons.append("Low P/B (" + str(round(pb, 2)) + ")")
    if ps and 0 < ps < 2:
        score += 1; reasons.append("Low P/S (" + str(round(ps, 2)) + ")")

    # --- 2. Dividend & cash flow ---
    # yfinance may return dividend yield as a fraction (0.04) or a percent (4.0)
    if div:
        div_pct = div if div > 1 else div * 100
        # Cap at 15% -- anything higher is almost always a data error for large caps
        if 3 < div_pct < 15:
            score += 1; reasons.append("Dividend yield " + str(round(div_pct, 1)) + "%")
    if fcf and mcap and fcf > 0:
        fcf_yield = fcf / mcap
        if fcf_yield > 0.05:
            score += 2; reasons.append("FCF yield " + str(round(fcf_yield * 100, 1)) + "%")
    if d2e is not None and 0 <= d2e < 50:
        score += 1; reasons.append("Low debt (D/E " + str(round(d2e, 0)) + ")")

    # --- 3. Growth at a reasonable price ---
    if peg and 0 < peg < 1:
        score += 3; reasons.append("PEG < 1 (" + str(round(peg, 2)) + ")")
    if rev_growth and rev_growth > 0.10:
        score += 1; reasons.append("Revenue growth " + str(round(rev_growth * 100, 0)) + "%")
    if earnings_growth and earnings_growth > 0.10:
        score += 1; reasons.append("Earnings growth " + str(round(earnings_growth * 100, 0)) + "%")

    # --- Analyst upside (bonus context) ---
    if target and price and target > price:
        upside = (target - price) / price * 100
        if upside > 15:
            score += 1; reasons.append("Analyst upside " + str(round(upside, 0)) + "%")

    return score, reasons


def scan_universe(tickers: list) -> list:
    """Pull stats for each ticker and score it. Returns sorted candidates."""
    results = []
    total = len(tickers)
    print("\nScanning " + str(total) + " stocks (this takes a few minutes)...\n")

    for i, ticker in enumerate(tickers, 1):
        try:
            info = yf.Ticker(ticker).info
            score, reasons = score_stock(info)
            if score >= 4:  # only keep stocks with meaningful signals
                results.append({
                    "ticker": ticker,
                    "name": info.get("longName", ticker),
                    "sector": info.get("sector", "Unknown"),
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "score": score,
                    "reasons": reasons,
                })
        except Exception:
            pass

        if i % 50 == 0:
            print("  ...scanned " + str(i) + "/" + str(total))

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── Claude writeup ────────────────────────────────────────────────────────────

def get_report(candidates: list) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    today = datetime.now().strftime("%B %d, %Y")
    lines = []
    for c in candidates:
        lines.append(
            c["ticker"] + " (" + c["name"] + ") | Sector: " + c["sector"]
            + " | Price: $" + str(c["price"])
            + " | Value signals: " + "; ".join(c["reasons"])
        )
    candidates_str = "\n".join(lines)

    prompt = (
        "You are a value investing analyst helping a beginner investor. Today is " + today + ".\n\n"
        "I screened the S&P 500 and Nasdaq 100 for undervalued stocks using value metrics, "
        "cash flow, and growth-at-a-reasonable-price. Here are " + str(len(candidates)) + " fresh names "
        "(stocks featured in the last " + str(REPEAT_WINDOW_DAYS) + " days are excluded so you keep learning new ones):\n\n"
        + candidates_str
        + "\n\nWrite a beginner-friendly report covering ALL " + str(len(candidates)) + " stocks above:\n\n"
        "1. The Picks -- for EACH stock: ticker, company, what it does, why it screened as undervalued "
        "(plain English), and one risk to be aware of\n"
        "2. Sector Patterns -- are these names clustering in any sector? What might that signal?\n"
        "3. A Beginner's Caution -- explain that 'undervalued' on paper doesn't always mean a good buy "
        "(value traps), and what to check before buying\n"
        "4. One to Research First -- pick the single most interesting name and say why it's worth a deeper look\n\n"
        "Explain every financial term in plain English (P/E, PEG, FCF, P/B, etc). "
        "Do not give direct buy/sell advice. Do not add disclaimers after section 4."
    )

    client = anthropic.Anthropic(api_key=api_key)
    parts = []
    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            parts.append(text)
    return "".join(parts)


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(report: str, candidate_count: int):
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    clean_report = report.encode("ascii", errors="replace").decode("ascii")
    date_str = datetime.now().strftime("%B %d, %Y")

    body = (
        "Undervalued Stock Screener - " + date_str + "\n"
        + str(candidate_count) + " stocks flagged with undervaluation signals.\n\n"
        + clean_report
        + "\n\n---\nGenerated by Undervalued Screener - claude-opus-4-8"
    )

    msg = EmailMessage()
    msg["Subject"] = "Undervalued Stock Screener - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nScreener report emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    send_to_email = "--email" in sys.argv[1:]

    print("\n" + "=" * 60)
    print("  UNDERVALUED STOCK SCREENER -- S&P 500 + Nasdaq 100")
    print("=" * 60 + "\n")

    print("Building stock universe...")
    universe = get_universe()
    if not universe:
        print("Could not build universe. Exiting.")
        sys.exit(1)

    candidates = scan_universe(universe)
    print("\n  Found " + str(len(candidates)) + " stocks with undervaluation signals")

    if not candidates:
        print("No undervalued candidates found today.")
        return

    # Exclude stocks featured recently so each run teaches new names
    recent = load_recent_tickers()
    fresh = [c for c in candidates if c["ticker"] not in recent]
    print("  " + str(len(recent)) + " excluded as recently featured; " + str(len(fresh)) + " fresh candidates")

    if not fresh:
        print("All strong candidates were featured recently. Try again after the window resets.")
        return

    # Take the top N fresh names by signal strength
    selected = fresh[:TOP_N]

    print("\nThis run's picks:")
    for c in selected:
        print("  [" + str(c["score"]) + "] " + c["ticker"] + " -- " + ", ".join(c["reasons"]))

    print("\nAsking Claude for analysis (streaming)...\n")
    print("=" * 60 + "\n")
    report = get_report(selected)

    print("\n\n" + "=" * 60)
    print("  Report generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    # Remember these so they aren't repeated next run
    record_featured([c["ticker"] for c in selected])

    if send_to_email:
        send_email(report, len(selected))


if __name__ == "__main__":
    run()
