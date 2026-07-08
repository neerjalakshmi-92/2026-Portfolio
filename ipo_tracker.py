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
import json
import smtplib
import requests
import anthropic
from datetime import datetime, timedelta
from email.message import EmailMessage
EDGAR_HEADERS = {"User-Agent": "InvestmentAgents neerja.lakshmi@gmail.com"}

# NewsAPI search queries for fresh IPO coverage
NEWSAPI_QUERIES = [
    "IPO",
    "initial public offering",
    "files to go public",
    "IPO pricing",
    "stock market debut",
]

# How many days of news each report should cover (runs Mon/Thu/Sat)
NEWS_LOOKBACK_DAYS = 4

# Remember companies covered within this many days to avoid repeating info
REPEAT_WINDOW_DAYS = 21

# File that remembers recently covered companies (committed back to the repo)
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "ipo_history.json")


# ── History (avoid repeating the same IPO info) ───────────────────────────────

def load_recent_companies() -> list:
    """Return company names covered within the last REPEAT_WINDOW_DAYS."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
    except Exception:
        return []

    cutoff = datetime.now() - timedelta(days=REPEAT_WINDOW_DAYS)
    recent = []
    for entry in history:
        try:
            if datetime.strptime(entry["date"], "%Y-%m-%d") >= cutoff:
                recent.append(entry["company"])
        except Exception:
            continue
    return recent


def record_covered(companies: list):
    """Append today's covered companies to history and prune old entries."""
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            history = []

    today = datetime.now().strftime("%Y-%m-%d")
    for c in companies:
        history.append({"company": c, "date": today})

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
    print("Recorded " + str(len(companies)) + " covered companies to history.")


def parse_covered(report: str) -> list:
    """Extract company names from the 'COVERED:' line Claude adds at the top."""
    for line in report.splitlines():
        if line.strip().upper().startswith("COVERED:"):
            raw = line.split(":", 1)[1]
            return [c.strip() for c in raw.split(";") if c.strip()]
    return []


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
    """Pull fresh IPO-related headlines directly from NewsAPI."""
    news_api_key = os.environ.get("NEWS_API_KEY")
    if not news_api_key:
        print("  NEWS_API_KEY not set -- skipping NewsAPI.")
        return []

    print("Searching NewsAPI for IPO news...")
    seen = set()
    headlines = []
    from_date = (datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    for query in NEWSAPI_QUERIES:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 15,
                    "from": from_date,
                },
                headers={"X-Api-Key": news_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            for article in resp.json().get("articles", []):
                title = article.get("title", "")
                source = article.get("source", {}).get("name", "Unknown")
                published = article.get("publishedAt", "")[:10]
                if title and title not in seen:
                    seen.add(title)
                    headlines.append(title + " (" + source + ", " + published + ")")
            print("  \"" + query + "\" scanned")
        except Exception as e:
            print("  \"" + query + "\" failed: " + str(e))

    return headlines


# ── Claude Synthesis ──────────────────────────────────────────────────────────

def get_ipo_report(filings: list, headlines: list, recent_companies: list) -> str:
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

    # Companies already covered recently
    already_str = ""
    if recent_companies:
        already_str = (
            "\n\nALREADY COVERED RECENTLY (do NOT re-introduce these from scratch -- only mention one "
            "if there is a GENUINELY NEW development since last time, e.g. it priced, set a date, or "
            "withdrew; otherwise skip it and prioritize NEW companies):\n"
            + "; ".join(sorted(set(recent_companies))) + "\n"
        )

    prompt = (
        "You are an IPO analyst covering all industries globally. Today is " + today + ".\n\n"
        "CRITICAL RULES:\n"
        "- Base your report ONLY on the SEC filings and news headlines provided below, plus the current date. "
        "Do NOT rely on your training data for IPO status -- it may be outdated.\n"
        "- Do NOT list any company that has ALREADY completed its IPO and is now publicly trading "
        "as 'upcoming' or 'rumored'. If a company already went public, it is done -- only mention it as "
        "historical context if directly relevant, never as a future opportunity.\n"
        "- Only treat a company as 'upcoming' or 'rumored' if the provided data actually supports that it is "
        "still private or pending as of " + today + ".\n"
        + already_str +
        "\n## Recent SEC S-1 Filings (companies registering to go public)\n"
        + filings_str
        + "\n\n## Recent IPO-Related News Headlines (last few days)\n"
        + headlines_str
        + "\n\nFIRST, output a single line listing every company you feature in this report, in EXACTLY "
        "this format (used by software; separate names with semicolons):\n"
        "COVERED: Company One; Company Two; Company Three\n\n"
        "THEN write a structured IPO Intelligence Report with these sections:\n\n"
        "1. Confirmed Pipeline (companies with active S-1 filings -- who they are, what they do, which industry)\n"
        "2. Upcoming to Watch (still-private companies with news suggesting imminent pricing or debut soon)\n"
        "3. Rumored Candidates (still-private companies mentioned as potential future IPOs -- industry and why)\n"
        "4. Recently Completed IPOs (companies from the news that JUST went public -- how the debut went)\n"
        "5. Market Conditions for IPOs (is the current environment favorable for new listings? Why or why not?)\n"
        "6. One IPO to Learn About (pick the most interesting STILL-PRIVATE company and give a 3-4 sentence "
        "beginner explainer)\n\n"
        "Cover all industries -- tech, biotech, consumer, energy, finance, space, everything. "
        "Be specific with company names where the data supports it. "
        "Keep it beginner-friendly -- explain any jargon. "
        "Do not add disclaimers or closing notes after section 6."
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
    # Strip the machine-readable COVERED line from the emailed version
    visible = "\n".join(
        line for line in report.splitlines()
        if not line.strip().upper().startswith("COVERED:")
    ).strip()
    clean_report = visible.encode("ascii", errors="replace").decode("ascii")
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

    recent = load_recent_companies()
    print("  " + str(len(recent)) + " companies covered recently (will avoid repeating)\n")

    print("Asking Claude for IPO analysis (streaming)...\n")
    print("=" * 60 + "\n")

    report = get_ipo_report(filings, headlines, recent)

    print("\n\n" + "=" * 60)
    print("  Report generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    # Remember which companies were covered so they aren't repeated
    covered = parse_covered(report)
    if covered:
        record_covered(covered)
    else:
        print("Warning: could not parse covered companies -- history not updated.")

    if send_to_email:
        send_email(report, len(filings))


if __name__ == "__main__":
    run()
