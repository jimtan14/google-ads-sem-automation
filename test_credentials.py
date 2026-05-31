"""Step 1 validation: confirm all Google Ads credentials work end-to-end.

Uses the SAME load_from_dict + env-var pattern as lib/google_ads_client.py
(and GitHub Actions), so a pass here means the agents will authenticate too.

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # then fill it in
    python test_credentials.py

Reads .env if present (no extra dependency: parsed manually).
"""

import os
import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

REQUIRED = [
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "GOOGLE_ADS_CUSTOMER_ID",
]


def load_dotenv() -> None:
    env = Path(__file__).parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def get_client() -> GoogleAdsClient:
    return GoogleAdsClient.load_from_dict(
        {
            "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
            "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
            "login_customer_id": os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
            "use_proto_plus": True,
        }
    )


def main() -> None:
    load_dotenv()

    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("Missing env vars: " + ", ".join(missing))
        sys.exit(1)

    customer_id = os.environ["GOOGLE_ADS_CUSTOMER_ID"]
    client = get_client()
    ga_service = client.get_service("GoogleAdsService")

    # Identity check: who am I, and can I read the AirOps account?
    rows = ga_service.search(
        customer_id=customer_id,
        query="""
            SELECT customer.id, customer.descriptive_name,
                   customer.currency_code, customer.time_zone
            FROM customer
        """,
    )
    for row in rows:
        c = row.customer
        print("✅ Credentials valid. Connected to:")
        print(f"   {c.descriptive_name} ({c.id}) — {c.currency_code}, {c.time_zone}")


if __name__ == "__main__":
    try:
        main()
    except GoogleAdsException as ex:
        print(f"❌ API error: {ex.error.code().name}")
        for error in ex.failure.errors:
            print(f"   - {error.message}")
        # Most common at this stage: developer token still at Test-access level.
        print("\nIf this says the developer token is not approved for the customer,")
        print("you're still on Test access — wait for Basic access approval.")
        sys.exit(1)
