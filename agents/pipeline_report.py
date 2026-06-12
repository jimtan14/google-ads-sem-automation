"""Agent 8 — Weekly Pipeline Report. Runs Mondays 9am PT.

A 6-stage HubSpot funnel for Paid Search (= Google Ads), last 7 days vs the prior
7 days (week-over-week Δ). Counts come from the HubSpot CRM Search API `total`.
This is a metrics digest — it reports, it doesn't recommend.

Funnel (contact source = Paid Search; deal channel = Paid Search):
  🧲 Leads → 📈 MQL → 📅 Meetings booked (Calendly) → 💡 Opportunities →
  🤝 Deals → ✅ Qualified deals (entered Discovery).

Definitions (see also the brand 'Original Traffic Source Channel' / 'Deal Channel'):
  Leads/MQL/Opportunities = contacts entering that lifecycle stage in the window.
  Meetings booked         = contacts whose 'Calendly - First Meeting Date Booked' is in the window.
  Deals                   = deals created in the window.
  Qualified deals         = deals entering the Discovery stage (Commercial Sales Pipeline).
Set PIPELINE_CHANNEL=all to drop the Paid Search filter and report all sources.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from lib.local_env import load_dotenv
from lib import hubspot_client as hs
from lib import slack_client as slack

load_dotenv()

CHANNEL = os.environ.get("PIPELINE_CHANNEL", "Paid Search")
CONTACT_CHANNEL_PROP = "original_traffic_source_channel"
DEAL_CHANNEL_PROP = "deal_channel"
DISCOVERY_DATE = "hs_v2_date_entered_44043869"  # entered "Discovery", Commercial Sales Pipeline

# (label, object_type, date_property, is_deal)
METRICS = [
    ("🧲 Leads",          "contacts", "hs_v2_date_entered_lead", False),
    ("📈 MQL",            "contacts", "hs_v2_date_entered_marketingqualifiedlead", False),
    ("📅 Meetings",       "contacts", "calendly___first_meeting_date", False),
    ("💡 Opportunities",  "contacts", "hs_v2_date_entered_opportunity", False),
    ("🤝 Deals",          "deals",    "createdate", True),
    ("✅ Qualified",      "deals",    DISCOVERY_DATE, True),
]


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def metric_count(object_type: str, date_prop: str, is_deal: bool, start: datetime, end: datetime) -> int:
    filters = [hs.between(date_prop, ms(start), ms(end))]
    if CHANNEL.lower() != "all":
        filters.append(hs.eq(DEAL_CHANNEL_PROP if is_deal else CONTACT_CHANNEL_PROP, CHANNEL))
    return hs.count(object_type, filters)


def delta(cur: int, prev: int) -> str:
    d = cur - prev
    if prev == 0:
        return f"+{d} (new)" if d else "0"
    sym = "▲" if d > 0 else ("▼" if d < 0 else "—")
    return f"{d:+d} {sym}{abs(d / prev * 100):.0f}%"


def pct(a: int, b: int) -> str:
    return f"{a / b * 100:.0f}%" if b else "—"


def main() -> None:
    now = datetime.now(timezone.utc)
    cur_start, cur_end = now - timedelta(days=7), now
    prev_start, prev_end = now - timedelta(days=14), now - timedelta(days=7)

    vals: dict[str, int] = {}
    rows = []
    for label, obj, prop, is_deal in METRICS:
        cur = metric_count(obj, prop, is_deal, cur_start, cur_end)
        prev = metric_count(obj, prop, is_deal, prev_start, prev_end)
        vals[label] = cur
        rows.append([label, cur, prev, delta(cur, prev)])

    name = CHANNEL if CHANNEL.lower() != "all" else "All Sources"
    blocks = [
        slack.header(f"📊 Weekly Pipeline Report — {name}"),
        slack.section(f"Week of *{cur_start:%b %d}–{cur_end:%b %d, %Y}* vs prior 7 days · _HubSpot live_"),
    ]
    for tbl in slack.table(["Stage", "L7D", "Prev 7D", "Δ vs prior"], rows, [16, 6, 8, 14]):
        blocks.append(slack.section(tbl))
    blocks.append(slack.section(
        f"*Conversion (L7D):*  Lead→MQL {pct(vals['📈 MQL'], vals['🧲 Leads'])}"
        f"  ·  MQL→Meeting {pct(vals['📅 Meetings'], vals['📈 MQL'])}"
        f"  ·  Deal→Discovery {pct(vals['✅ Qualified'], vals['🤝 Deals'])}"
    ))
    blocks.append(slack.context(
        f"Source: {name} · Meetings = Calendly first-meeting-date booked · "
        f"Opportunities = contacts entering Opportunity stage · Weekly Pipeline Report · Mondays 9am PT"
    ))
    slack.send_message(
        blocks,
        text=f"[PIPELINE] {name}: {vals['🧲 Leads']} leads, {vals['🤝 Deals']} deals, {vals['✅ Qualified']} qualified (L7D)",
    )


if __name__ == "__main__":
    main()
