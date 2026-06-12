"""Shared HubSpot CRM client. Auth via HUBSPOT_API_KEY (private-app token).

Uses the CRM Search API, which returns a `total` for any filter set — so metric
counts come straight from `total` (limit=1), never by paging every record.
"""
from __future__ import annotations

import os

import requests

BASE = "https://api.hubapi.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}",
        "Content-Type": "application/json",
    }


def count(object_type: str, filters: list[dict]) -> int:
    """Number of `object_type` (e.g. 'contacts', 'deals') records matching ALL
    `filters` (AND). Each filter is a HubSpot search filter dict — see between()/eq()."""
    resp = requests.post(
        f"{BASE}/crm/v3/objects/{object_type}/search",
        headers=_headers(),
        json={"filterGroups": [{"filters": filters}], "limit": 1, "properties": ["hs_object_id"]},
        timeout=30,
    )
    resp.raise_for_status()
    return int(resp.json().get("total", 0))


def between(prop: str, start_ms: int, end_ms: int) -> dict:
    """A datetime BETWEEN filter. HubSpot wants epoch-millisecond strings."""
    return {"propertyName": prop, "operator": "BETWEEN", "value": str(start_ms), "highValue": str(end_ms)}


def eq(prop: str, value: str) -> dict:
    return {"propertyName": prop, "operator": "EQ", "value": value}
