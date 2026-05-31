"""Generate an OAuth2 refresh token for the Google Ads API.

Run this ONCE after creating an OAuth2 client in Google Cloud Console.
It opens a browser, asks you to authorize the AirOps Google account, and
prints a refresh token. Paste that token into google-ads.yaml.

Usage:
    python generate_refresh_token.py --client_id XXX --client_secret YYY
"""

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

# Full Google Ads API access.
SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main(client_id: str, client_secret: str) -> None:
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    # Opens a local browser; prompt=consent forces a refresh_token to be returned.
    flow.run_local_server(
        prompt="consent",
        access_type="offline",
        authorization_prompt_message="Authorize with the AirOps Google account: {url}",
    )

    creds = flow.credentials
    print("\n" + "=" * 60)
    print("Refresh token (paste into google-ads.yaml):\n")
    print(creds.refresh_token)
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a Google Ads refresh token.")
    parser.add_argument("--client_id", required=True, help="OAuth2 client ID")
    parser.add_argument("--client_secret", required=True, help="OAuth2 client secret")
    args = parser.parse_args()
    main(args.client_id, args.client_secret)
