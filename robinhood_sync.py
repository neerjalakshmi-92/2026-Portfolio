"""
Robinhood Sync -- READ-ONLY pull of your live Robinhood positions.

Logs into Robinhood (read-only), fetches your current holdings, and
writes them to portfolio.json. The portfolio_tracker.py script then
reads that file, so your P&L always reflects your real positions
without manually editing code.

IMPORTANT:
- This uses an UNOFFICIAL Robinhood API (robin_stocks). It only READS
  data -- it never places trades.
- Credentials come from environment variables, never hardcoded.

Required environment variables:
  ROBINHOOD_USERNAME   -- your Robinhood email
  ROBINHOOD_PASSWORD   -- your Robinhood password
  ROBINHOOD_MFA        -- (optional) your TOTP secret if 2FA is enabled

Usage:
  python3 robinhood_sync.py
"""

import os
import sys
import json
import robin_stocks.robinhood as rh

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")


def login_to_robinhood():
    """Log into Robinhood using credentials from environment variables."""
    username = os.environ.get("ROBINHOOD_USERNAME")
    password = os.environ.get("ROBINHOOD_PASSWORD")
    mfa_secret = os.environ.get("ROBINHOOD_MFA")

    if not username or not password:
        print("ROBINHOOD_USERNAME or ROBINHOOD_PASSWORD not set.")
        print("Set them first:")
        print("  export ROBINHOOD_USERNAME='your-email'")
        print("  export ROBINHOOD_PASSWORD='your-password'")
        sys.exit(1)

    mfa_code = None
    if mfa_secret:
        import pyotp
        mfa_code = pyotp.TOTP(mfa_secret).now()
        print("Generated MFA code from TOTP secret.")

    print("Logging into Robinhood (read-only)...")
    try:
        rh.login(username=username, password=password, mfa_code=mfa_code)
        print("Login successful.\n")
    except Exception as e:
        print("Login failed: " + str(e))
        print("\nIf you have 2FA via SMS/email, you may be prompted in the terminal.")
        print("For automated runs, set up an authenticator app and use ROBINHOOD_MFA.")
        sys.exit(1)


def fetch_positions() -> dict:
    """Pull current holdings and format them for portfolio_tracker."""
    print("Fetching your positions...")
    holdings = rh.build_holdings()

    portfolio = {}
    for ticker, data in holdings.items():
        shares = float(data.get("quantity", 0))
        avg_cost = float(data.get("average_buy_price", 0))
        if shares > 0:
            portfolio[ticker] = {
                "shares": round(shares, 4),
                "avg_cost": round(avg_cost, 2),
            }
            print("  " + ticker + ": " + str(round(shares, 4)) + " shares @ $" + str(round(avg_cost, 2)))

    return portfolio


def save_portfolio(portfolio: dict):
    """Write the portfolio to portfolio.json."""
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)
    print("\nSaved " + str(len(portfolio)) + " positions to portfolio.json")


def run():
    print("\n" + "=" * 60)
    print("  ROBINHOOD SYNC -- Read-Only Portfolio Pull")
    print("=" * 60 + "\n")

    login_to_robinhood()
    portfolio = fetch_positions()

    if not portfolio:
        print("\nNo positions found.")
        return

    save_portfolio(portfolio)

    # Log out to clean up the session
    rh.logout()
    print("Logged out. Your portfolio_tracker will now use these positions.")


if __name__ == "__main__":
    run()
