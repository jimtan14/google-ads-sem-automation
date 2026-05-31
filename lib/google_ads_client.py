"""Shared Google Ads API wrapper. Auth via env vars (same names as GitHub Secrets)."""
import os

from google.ads.googleads.client import GoogleAdsClient
from google.protobuf.json_format import MessageToDict


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


def run_query(customer_id: str, query: str) -> list[dict]:
    """Run a GAQL query and return rows as plain dicts with camelCase keys that
    mirror the API field names (e.g. row["searchTermView"]["searchTerm"],
    row["metrics"]["costMicros"]). Uses search_stream so large result sets page
    automatically."""
    client = get_client()
    service = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    for batch in service.search_stream(customer_id=customer_id, query=query):
        for row in batch.results:
            rows.append(MessageToDict(row._pb))
    return rows
