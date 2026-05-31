"""Shared Slack delivery via Incoming Webhook (Block Kit).

The webhook must be bound to #airops-paidads-shared-main. Because that channel is
private, whoever creates the Incoming Webhook in the Slack app config must be a
member of it.

Set DRY_RUN=1 (or leave SLACK_WEBHOOK_URL unset) to print the payload instead of
posting — useful for local testing.
"""
from __future__ import annotations

import json
import os
import re

import requests

_NUMERIC = re.compile(r"^[\$+\-]?[\d,]+(\.\d+)?%?$|^—$")


def table(headers: list[str], rows: list[list], maxw: list[int] | None = None,
          limit_chars: int = 2700) -> list[str]:
    """Render an aligned monospace table (padded columns, header rule, numbers
    right-aligned) wrapped in ``` code blocks, chunked under Slack's char limit.
    `maxw` caps each column's width (long cells are truncated with …)."""
    n = len(headers)
    maxw = maxw or [80] * n
    trows = []
    for r in rows:
        tr = []
        for i in range(n):
            s = str(r[i])
            tr.append(s if len(s) <= maxw[i] else s[: maxw[i] - 1] + "…")
        trows.append(tr)
    right = []
    for i in range(n):
        cells = [tr[i] for tr in trows if tr[i] not in ("", "—")]
        right.append(bool(cells) and all(_NUMERIC.match(c) for c in cells))
    widths = [len(headers[i]) for i in range(n)]
    for tr in trows:
        for i in range(n):
            widths[i] = max(widths[i], len(tr[i]))

    def fmt(cells):
        parts = [(str(cells[i]).rjust(widths[i]) if right[i] else str(cells[i]).ljust(widths[i]))
                 for i in range(n)]
        return "| " + " | ".join(parts) + " |"

    header_line = fmt(headers)
    sep = "|" + "|".join("-" * (widths[i] + 2) for i in range(n)) + "|"
    chunks, cur, cur_len = [], [header_line, sep], len(header_line) + len(sep)
    for tr in trows:
        line = fmt(tr)
        if cur_len + len(line) + 1 > limit_chars and len(cur) > 2:
            chunks.append("```\n" + "\n".join(cur) + "\n```")
            cur, cur_len = [header_line, sep], len(header_line) + len(sep)
        cur.append(line)
        cur_len += len(line) + 1
    if len(cur) > 2:
        chunks.append("```\n" + "\n".join(cur) + "\n```")
    return chunks


def send_message(blocks: list, text: str = "Paid Ads report") -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    payload = {"text": text, "blocks": blocks}  # `text` is the notification fallback
    if not url or os.environ.get("DRY_RUN") == "1":
        print(json.dumps(payload, indent=2))
        return
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    resp.raise_for_status()


def grouped_tables(rows: list[list], group_idx: int, headers: list[str],
                   maxw: list[int] | None = None, order_key=None) -> list[dict]:
    """Group rows by the value at `group_idx` (e.g. campaign), emit a bold
    sub-header per group, then an aligned table of that group's rows with the
    group column removed. `order_key` orders the groups (else insertion order).
    Rows should be pre-sorted within group."""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(str(r[group_idx]), []).append(
            [c for i, c in enumerate(r) if i != group_idx]
        )
    names = sorted(groups, key=order_key) if order_key else list(groups)
    blocks = []
    for g in names:
        blocks.append(section(f"*{g}*"))
        for tbl in table(headers, groups[g], maxw):
            blocks.append(section(tbl))
    return blocks


def short_campaign(c: str) -> str:
    """NA_en_Google_BOF_Search_NonBrand_Generic_AEO -> 'NA NonBrand_Generic_AEO'."""
    return re.sub(r"_[a-z]{2}_Google_[A-Z]+_Search_", " ", c or "")


def campaign_group_rank(g: str):
    """Order short campaign group names: NA before EU; within region
    Brand → NonBrand Generic/AEO → Competitor → other."""
    low = g.lower()
    region = 0 if low.startswith("na") else (1 if low.startswith("eu") else 2)
    if "competitor" in low:
        t = 2
    elif "aeo" in low or "generic" in low:
        t = 1
    elif "brand" in low and "nonbrand" not in low:
        t = 0
    else:
        t = 3
    return (region, t, g)


def header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def section(mrkdwn: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn[:3000]}}


def context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def divider() -> dict:
    return {"type": "divider"}
