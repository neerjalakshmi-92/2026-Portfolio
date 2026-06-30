"""
IPO Tracker -- tracks confirmed and rumored IPOs across all industries.

Two data sources:
1. SEC EDGAR -- recent S-1 filings (companies actively registering to go public)
2. News -- IPO-related headlines from ETFs and market sources

Asks Claude to synthesize both into a clean report covering confirmed
IPOs, upcoming pricings, and rumored candidates.

Usage:
  python3 ipo_tracker.py           # print to terminal
  python3 ipo_tracker.py --email   # print + email
"""

import os
import sys
import smtplib
import requests
import anthropic
from datetime import datetime, timedelta
from email.message import EmailMessage
from fetch_stock_data import fetch_stock_data

EDGAR_HEADERS = {"User-Agent": "InvestmentAgents neerja.lakshmi@gmail.com"}

# ETFs and tickers that carry IPO-related news
IPO_NEWS_TICKERS = ["IPO", "IPOS", "SPY", "QQQ"]


# ── SEC EDGAR S-1 Filings ─────────────────────────────────────────────────────

def get_recent_s1_filings(days_back: int = 14) -> list:
    """
    Fetch recent S-1 and S-1/A filings from SEC EDGAR.
    S-1 = initial IPO registration. S-1/A = amendment (company is actively
    updating their filing, usually close to pricing).
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    url = (
        "https://efts.sec.gov/LATEST/search-index?q=&forms=S-1,S-1%2FA"
        "&dateRange=custom&startdt=" + start_date + "&enddt=" + end_date
        + "&hits.hits._source=period_of_report,file_date,display_names,form_type,biz_location"
        "&hits.hits.total=true&hits.hits.highlight=false"
        + "&hits.hits.hits._source=period_of_report,file_date,display_names,form_type"
    )

    # Use the EDGAR full text search API
    search_url = (
        "https://efts.sec.gov/LATEST/search-index?"
        "q=&forms=S-1%2CS-1%2FA"
        "&dateRange=custom"
        "&startdt=" + start_date
        + "&enddt=" + end_date
    )

    try:
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": "",
                "forms": "S-1,S-1/A",
                "dateRange": "custom",
                "startdt": start_date,
                "enddt": end_date,
            },
            headers=EDGAR_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        filings = []
        for hit in hits[:30]:
            source = hit.get("_source", {})
            company = source.get("display_names", ["Unknown"])[0] if source.get("display_names") else "Unknown"
            form_type = source.get("form_type", "S-1")
            file_date = source.get("file_date", "")
            filings.append({
                "company": company,
                "form_type": form_type,
                "file_date": file_date,
            })

        return filings

    except Exception as e:
        print("  Could not fetch SEC EDGAR filings: " + str(e))
        return []


# ── IPO News ──────────────────────────────────────────────────────────────────

def get_ipo_news() -> list:
    """Pull IPO-related headlines from ETFs and market trackers."""
    all_headlines = []
    for ticker in IPO_NEWS_TICKERS:
        try:
            data = fetch_stock_data(ticker)
            for article in data["news"][:3]:
                title_lower = article["title"].lower()
                # Only keep headlines that mention IPO-related terms
                if any(word in title_lower for word in ["ipo", "public", "listing", "offering", "s-1", "debut", "spac"]):
                    all_headlines.append(
                        "[" + ticker + "] " + article["title"] + " (" + article["source"] + ", " + article["date"] + ")"
                    )
        except Exception as e:
            print("  " + ticker + " news skipped: " + str(e))
    return all_headlines


# ── Claude Synthesis ──────────────────────────────────────────────────────────

def get_ipo_report(filings: list, headlines: list) -> str:
    """Ask Claude to synthesize S-1 filings and news into a clean IPO report."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    today = datetime.now().strftime("%B %d, %Y")

    # Format filings
    if filings:
        filings_str = "\n".join(
            "- " + f["company"] + " (Form: " + f["form_type"] + ", Filed: " + f["file_date"] + ")"
            for f in filings
        )
    else:
        filings_str = "No new S-1 filings found in the past 14 days."

    # Format headlines
    if headlines:
        headlines_str = "\n".join("- " + h for h in headlines)
    else:
        headlines_str = "No IPO-related headlines found today."

    prompt = (
        "You are an IPO analyst covering all industries globally. Today is " + today + ".\n\n"
        "## Recent SEC S-1 Filings (companies registering to go public)\n"
        + filings_str
        + "\n\n## IPO-Related News Headlines\n"
        + headlines_str
        + "\n\nWrite a structured IPO Intelligence Report with these sections:\n\n"
        "1. Confirmed Pipeline (companies with active S-1 filings -- who they are, what they do, which industry)\n"
        "2. Upcoming to Watch (filings or news suggesting imminent pricing or debut in the next 30 days)\n"
        "3. Rumored Candidates (companies mentioned in news as potential IPO candidates -- include industry and why they might go public)\n"
        "4. Market Conditions for IPOs (is the current environment favorable for new listings? Why or why not?)\n"
        "5. One IPO to Learn About (pick the most interesting company from the list and give a 3-4 sentence explainer for a beginner investor)\n\n"
        "Cover all industries -- tech, biotech, consumer, energy, finance, space, everything. "
        "Be specific with company names where the data supports it. "
        "Keep it beginner-friendly -- explain any jargon. "
        "Do not add disclaimers or closing notes after section 5."
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


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(report: str, filing_count: int):
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    clean_report = report.encode("ascii", errors="replace").decode("ascii")
    date_str = datetime.now().strftime("%B %d, %Y")

    body = (
        "IPO Intelligence Report - " + date_str + "\n"
        + str(filing_count) + " new S-1 filings tracked in the past 14 days.\n\n"
        + clean_report
        + "\n\n---\nGenerated by IPO Tracker - claude-opus-4-8"
    )

    msg = EmailMessage()
    msg["Subject"] = "IPO Intelligence Report - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nIPO report emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    send_to_email = "--email" in sys.argv[1:]

    print("\n" + "=" * 60)
    print("  IPO TRACKER -- All Industries")
    print("=" * 60 + "\n")

    print("Fetching recent SEC S-1 filings (past 14 days)...")
    filings = get_recent_s1_filings(days_back=14)
    print("  Found " + str(len(filings)) + " S-1 filings\n")

    print("Gathering IPO-related news...")
    headlines = get_ipo_news()
    print("  Found " + str(len(headlines)) + " relevant headlines\n")

    print("Asking Claude for IPO analysis (streaming)...\n")
    print("=" * 60 + "\n")

    report = get_ipo_report(filings, headlines)

    print("\n\n" + "=" * 60)
    print("  Report generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    if send_to_email:
        send_email(report, len(filings))


if __name__ == "__main__":
    run()
