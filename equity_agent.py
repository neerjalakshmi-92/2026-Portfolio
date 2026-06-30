"""
Equity Analyst Agent -- deep-dive analysis on a single stock.

Fetches price data, pulls the latest 10-K from SEC EDGAR, and asks
Claude to analyze the company's fundamentals, moat, and risks.

Usage:
  python3 equity_agent.py NVDA
  python3 equity_agent.py AAPL --email
"""

import os
import sys
import json
import smtplib
import requests
import anthropic
from datetime import datetime
from email.message import EmailMessage
from fetch_stock_data import fetch_stock_data

EDGAR_HEADERS = {"User-Agent": "InvestmentAgents neerja.lakshmi@gmail.com"}
TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"


# ── SEC EDGAR helpers ────────────────────────────────────────────────────────

def get_cik(ticker: str) -> str:
    """Look up a company's CIK number from its ticker symbol."""
    resp = requests.get(TICKER_CIK_URL, headers=EDGAR_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry["ticker"].upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)
    raise ValueError("Could not find CIK for ticker: " + ticker)


def get_latest_10k_text(cik: str, max_chars: int = 12000) -> str:
    """
    Fetch the most recent 10-K filing from SEC EDGAR and return
    a trimmed excerpt suitable for Claude's context window.
    """
    submissions_url = "https://data.sec.gov/submissions/CIK" + cik + ".json"
    resp = requests.get(submissions_url, headers=EDGAR_HEADERS, timeout=10)
    resp.raise_for_status()
    submissions = resp.json()

    # Find the most recent 10-K in the filings list
    filings = submissions.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accession_numbers = filings.get("accessionNumber", [])
    primary_documents = filings.get("primaryDocument", [])

    ten_k_index = None
    for i, form in enumerate(forms):
        if form == "10-K":
            ten_k_index = i
            break

    if ten_k_index is None:
        return "No 10-K filing found for this company."

    accession = accession_numbers[ten_k_index].replace("-", "")
    primary_doc = primary_documents[ten_k_index]
    company_name = submissions.get("name", "Unknown")
    filing_date = filings.get("filingDate", [])[ten_k_index]

    doc_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        + str(int(cik))
        + "/" + accession + "/" + primary_doc
    )

    print("  Fetching 10-K for " + company_name + " (filed " + filing_date + ")...")
    doc_resp = requests.get(doc_url, headers=EDGAR_HEADERS, timeout=30)
    doc_resp.raise_for_status()

    # Strip HTML tags for plain text
    text = doc_resp.text
    import re
    text = re.sub(r"<[^>]+>", " ", text)           # remove HTML tags
    text = re.sub(r"&[a-zA-Z]+;", " ", text)        # remove HTML entities
    text = re.sub(r"\s+", " ", text).strip()         # collapse whitespace

    # Return a trimmed excerpt -- enough for Claude to get the key sections
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... 10-K truncated for length ...]"
    return text


# ── Analysis ─────────────────────────────────────────────────────────────────

def build_equity_prompt(ticker: str, stock_data: dict, ten_k_text: str) -> str:
    price = stock_data["price"]
    fund = stock_data["fundamentals"]
    news = stock_data["news"]

    change_str = "N/A"
    if price["change_pct"] is not None:
        direction = "up" if price["change_pct"] >= 0 else "down"
        change_str = direction + " " + str(abs(price["change_pct"])) + "% today"

    range_str = ""
    hi = fund.get("52w_high")
    lo = fund.get("52w_low")
    if hi and lo and price["current"] and hi != lo:
        pct = (price["current"] - lo) / (hi - lo) * 100
        range_str = "  52-week position: " + str(round(pct)) + "% of range ($" + str(lo) + " - $" + str(hi) + ")\n"

    fundamentals_str = ""
    if fund.get("market_cap"):
        fundamentals_str += "  Market Cap: $" + str(round(fund["market_cap"] / 1e9, 1)) + "B\n"
    if fund.get("pe_ratio"):
        fundamentals_str += "  P/E (trailing): " + str(fund["pe_ratio"]) + "\n"
    if fund.get("forward_pe"):
        fundamentals_str += "  P/E (forward): " + str(fund["forward_pe"]) + "\n"
    if fund.get("eps"):
        fundamentals_str += "  EPS: $" + str(fund["eps"]) + "\n"
    if fund.get("sector"):
        fundamentals_str += "  Sector: " + str(fund["sector"]) + " / " + str(fund.get("industry", "")) + "\n"

    news_str = ""
    if news:
        news_str = "Recent News:\n"
        for article in news[:5]:
            news_str += "  - [" + article["date"] + "] " + article["title"] + " (" + article["source"] + ")\n"

    price_block = (
        "Price: $" + str(price["current"]) + " (" + change_str + ")\n"
        + range_str
        + fundamentals_str
        + news_str
    )

    return (
        "You are a senior equity analyst. Produce a deep-dive report on " + ticker.upper() + ".\n\n"
        "## Live Market Data\n" + price_block + "\n"
        "## 10-K Excerpt (most recent annual report)\n" + ten_k_text + "\n\n"
        "Write a structured equity analysis with these sections:\n\n"
        "1. Company Overview (2-3 sentences: what they do, how they make money)\n"
        "2. Competitive Moat (what structural advantages protect this business)\n"
        "3. Financial Health (interpret the fundamentals and what the 10-K reveals)\n"
        "4. Recent Developments (what the news tells us about near-term momentum)\n"
        "5. Key Risks (3-4 specific risks grounded in the data)\n"
        "6. Verdict (Bull case / Bear case / Overall stance in 2-3 sentences)\n\n"
        "Be specific. Cite numbers from the data where possible. "
        "Do not add disclaimers or closing notes after the Verdict."
    )


def get_analysis(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        sys.exit(1)

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


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(ticker: str, report: str):
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    clean_report = report.encode("ascii", errors="replace").decode("ascii")
    date_str = datetime.now().strftime("%B %d, %Y")

    body = (
        "Equity Analysis: " + ticker.upper() + " - " + date_str + "\n\n"
        + clean_report
        + "\n\n---\nGenerated by Equity Analyst Agent - claude-opus-4-8"
    )

    msg = EmailMessage()
    msg["Subject"] = "Equity Analysis: " + ticker.upper() + " - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nAnalysis emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    args = [a for a in sys.argv[1:] if a != "--email"]
    send_to_email = "--email" in sys.argv[1:]

    if not args:
        print("Usage: python3 equity_agent.py TICKER [--email]")
        print("Example: python3 equity_agent.py NVDA --email")
        sys.exit(1)

    ticker = args[0].upper()

    print("\n" + "=" * 60)
    print("  EQUITY ANALYST -- " + ticker)
    print("=" * 60 + "\n")

    # Step 1: Live market data
    print("Fetching live market data...")
    try:
        stock_data = fetch_stock_data(ticker)
    except Exception as e:
        print("Could not fetch market data: " + str(e))
        sys.exit(1)

    # Step 2: SEC EDGAR 10-K
    print("Looking up SEC EDGAR filing...")
    try:
        cik = get_cik(ticker)
        ten_k_text = get_latest_10k_text(cik)
    except Exception as e:
        print("  Could not fetch 10-K: " + str(e) + " -- continuing without it.")
        ten_k_text = "10-K not available."

    # Step 3: Claude analysis
    print("\nAsking Claude for deep-dive analysis (streaming)...\n")
    print("=" * 60 + "\n")

    prompt = build_equity_prompt(ticker, stock_data, ten_k_text)
    report = get_analysis(prompt)

    print("\n\n" + "=" * 60)
    print("  Analysis generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    if send_to_email:
        send_email(ticker, report)


if __name__ == "__main__":
    run()
