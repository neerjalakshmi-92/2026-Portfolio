"""
OpenAI Tracker -- monitors news and IPO developments for OpenAI.

Since OpenAI is private (no ticker), this pulls news from related
public companies (Microsoft, NVIDIA, Google, Meta) and filters for
OpenAI mentions. Also checks SEC EDGAR for any S-1 filing activity.

Usage:
  python3 openai_tracker.py           # print to terminal
  python3 openai_tracker.py --email   # print + email
"""

import os
import sys
import smtplib
import requests
import anthropic
from datetime import datetime, timedelta
from email.message import EmailMessage
EDGAR_HEADERS = {"User-Agent": "InvestmentAgents neerja.lakshmi@gmail.com"}

# NewsAPI search queries
NEWSAPI_QUERIES = [
    "OpenAI",
    "Sam Altman",
    "ChatGPT IPO",
    "OpenAI IPO",
]


# ── News Collection ───────────────────────────────────────────────────────────

def gather_openai_news() -> list:
    """Search NewsAPI directly for OpenAI-related headlines."""
    news_api_key = os.environ.get("NEWS_API_KEY")
    if not news_api_key:
        print("  NEWS_API_KEY not set -- skipping NewsAPI.")
        return []

    print("Searching NewsAPI for OpenAI news...\n")
    seen = set()
    headlines = []

    for query in NEWSAPI_QUERIES:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "from": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
                },
                headers={"X-Api-Key": news_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])

            for article in articles:
                title = article.get("title", "")
                source = article.get("source", {}).get("name", "Unknown")
                published = article.get("publishedAt", "")[:10]
                if title and title not in seen:
                    seen.add(title)
                    headlines.append("[" + query + "] " + title + " (" + source + ", " + published + ")")

            print("  \"" + query + "\" -- " + str(len(articles)) + " articles found")

        except Exception as e:
            print("  \"" + query + "\" -- failed: " + str(e))

    return headlines


# ── SEC EDGAR S-1 Check ───────────────────────────────────────────────────────

def check_edgar_for_openai() -> str:
    """Search SEC EDGAR for any OpenAI-related S-1 filings."""
    print("\nChecking SEC EDGAR for OpenAI filings...")
    try:
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": "OpenAI",
                "forms": "S-1,S-1/A",
                "dateRange": "custom",
                "startdt": (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
                "enddt": datetime.now().strftime("%Y-%m-%d"),
            },
            headers=EDGAR_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        if not hits:
            print("  No OpenAI S-1 filings found on EDGAR yet.")
            return "No S-1 or S-1/A filing from OpenAI found on SEC EDGAR in the past 180 days."

        lines = []
        for hit in hits[:5]:
            source = hit.get("_source", {})
            company = source.get("display_names", ["Unknown"])[0] if source.get("display_names") else "Unknown"
            form_type = source.get("form_type", "")
            file_date = source.get("file_date", "")
            lines.append("- " + company + " | " + form_type + " | Filed: " + file_date)

        print("  Found " + str(len(hits)) + " relevant filing(s)")
        return "\n".join(lines)

    except Exception as e:
        print("  EDGAR check failed: " + str(e))
        return "EDGAR check unavailable: " + str(e)


# ── Claude Synthesis ──────────────────────────────────────────────────────────

def get_openai_report(headlines: list, edgar_result: str) -> str:
    """Ask Claude to synthesize everything into an OpenAI intelligence report."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    today = datetime.now().strftime("%B %d, %Y")

    headlines_str = "\n".join("- " + h for h in headlines) if headlines else "No OpenAI-related headlines found today."

    prompt = (
        "You are an analyst specializing in AI companies and private market intelligence. Today is " + today + ".\n\n"
        "## SEC EDGAR Filing Check\n"
        + edgar_result
        + "\n\n## News Headlines Mentioning OpenAI\n"
        + headlines_str
        + "\n\nWrite a structured OpenAI Intelligence Report with these sections:\n\n"
        "1. IPO Status Update\n"
        "   - Has OpenAI filed an S-1 yet? What does the EDGAR data show?\n"
        "   - Based on the news, how close does OpenAI appear to be to going public?\n"
        "   - Any signals about timing, valuation, or structure (direct listing vs traditional IPO)?\n\n"
        "2. Latest News & Developments\n"
        "   - What is OpenAI doing right now? New products, partnerships, funding, controversies?\n"
        "   - Any news about Sam Altman or leadership?\n\n"
        "3. Competitive Landscape\n"
        "   - How are rivals (Google, Meta, Anthropic, Mistral) responding to OpenAI?\n"
        "   - Does the news suggest OpenAI is gaining or losing ground?\n\n"
        "4. What to Watch\n"
        "   - 2-3 specific signals or events that would indicate an IPO is getting closer\n"
        "   - Any risks that could delay or derail an OpenAI IPO\n\n"
        "5. Investor Takeaway\n"
        "   - For a beginner investor, what does all this mean?\n"
        "   - Is there any way to get indirect exposure to OpenAI today (through public stocks)?\n\n"
        "Be specific. Cite headlines where relevant. Explain all jargon in plain English. "
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

def send_email(report: str, headline_count: int):
    gmail_user = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("\nSkipping email -- GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set.")
        return

    gmail_password = gmail_password.replace("\xa0", " ").strip()
    clean_report = report.encode("ascii", errors="replace").decode("ascii")
    date_str = datetime.now().strftime("%B %d, %Y")

    body = (
        "OpenAI Intelligence Report - " + date_str + "\n"
        + str(headline_count) + " OpenAI-related headlines tracked today.\n\n"
        + clean_report
        + "\n\n---\nGenerated by OpenAI Tracker - claude-opus-4-8"
    )

    msg = EmailMessage()
    msg["Subject"] = "OpenAI Intelligence Report - " + date_str
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print("\nOpenAI report emailed to " + gmail_user)
    except Exception as e:
        print("\nEmail failed: " + str(e))


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    send_to_email = "--email" in sys.argv[1:]

    print("\n" + "=" * 60)
    print("  OPENAI TRACKER -- IPO & News Intelligence")
    print("=" * 60 + "\n")

    headlines = gather_openai_news()
    print("\n  Found " + str(len(headlines)) + " OpenAI-related headlines")

    edgar_result = check_edgar_for_openai()

    print("\nAsking Claude for OpenAI analysis (streaming)...\n")
    print("=" * 60 + "\n")

    report = get_openai_report(headlines, edgar_result)

    print("\n\n" + "=" * 60)
    print("  Report generated by claude-opus-4-8")
    print("=" * 60 + "\n")

    if send_to_email:
        send_email(report, len(headlines))


if __name__ == "__main__":
    run()
