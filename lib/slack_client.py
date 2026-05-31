"""Shared Slack delivery via Incoming Webhook (Block Kit).

The webhook must be bound to #airops-paidads-shared-main. Because that channel is
private, whoever creates the Incoming Webhook in the Slack app config must be a
member of it.

Set DRY_RUN=1 (or leave SLACK_WEBHOOK_URL unset) to print the payload instead of
posting — useful for local testing.
"""
import json
import os

import requests


def send_message(blocks: list, text: str = "Paid Ads report") -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    payload = {"text": text, "blocks": blocks}  # `text` is the notification fallback
    if not url or os.environ.get("DRY_RUN") == "1":
        print(json.dumps(payload, indent=2))
        return
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    resp.raise_for_status()


def header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def section(mrkdwn: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn[:3000]}}


def context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def divider() -> dict:
    return {"type": "divider"}
