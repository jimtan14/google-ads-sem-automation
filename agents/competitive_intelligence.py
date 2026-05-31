"""Agent 6 — Competitive Intelligence. Runs Thursdays 9am PT.

Auction Insights (competitor domains) is NOT available via the Google Ads API,
so this tracks our own impression-share competitiveness per campaign, L14D vs
P14D, and uses IS-lost-to-rank as the "competitors gaining" proxy (vs IS-lost-to-
budget, which is self-inflicted). Three parts:
  📊 IS by campaign — L14D with change vs the prior 14 days
  ⚔️ Competitive pressure — campaigns with IS swing > IS_SWING_PCT, rank vs budget
  🧭 Alerts + strategic recommendations (Claude)
Posts to #airops-paidads-shared-main. Recommends only; a human acts.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from lib.local_env import load_dotenv
from lib.google_ads_client import run_query
from lib.claude_client import analyze_json
from lib import slack_client as slack

load_dotenv()

CUSTOMER_ID = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "2965557823")
PROMPT = (ROOT / "prompts" / "competitive_intelligence.txt").read_text()

IS_SWING_PCT = float(os.environ.get("IS_SWING_PCT", "0.15"))    # highlight if |IS change| > 15%
CPC_SWING_PCT = float(os.environ.get("CI_CPC_SWING_PCT", "0.20"))  # or if CPC jumps > 20% (competitors bidding up)
MIN_IMPR = int(os.environ.get("CI_MIN_IMPR", "200"))            # need volume in BOTH periods
SENTINEL = 0.0999                                               # Google "<10%" floor

_Y = date.today() - timedelta(days=1)            # yesterday (today is partial)
_L_START, _L_END = _Y - timedelta(days=13), _Y   # last 14 days
_P_START, _P_END = _L_START - timedelta(days=14), _L_START - timedelta(days=1)  # prior 14


def is_query(start: date, end: date) -> str:
    return f"""
SELECT campaign.name, metrics.search_impression_share, metrics.search_top_impression_share,
       metrics.search_rank_lost_impression_share, metrics.search_budget_lost_impression_share,
       metrics.average_cpc, metrics.impressions
FROM campaign
WHERE segments.date BETWEEN '{start:%Y-%m-%d}' AND '{end:%Y-%m-%d}'
  AND campaign.advertising_channel_type = 'SEARCH' AND metrics.impressions > 0
"""


def fetch(start, end) -> dict[str, dict]:
    out = {}
    for r in run_query(CUSTOMER_ID, is_query(start, end)):
        m = r.get("metrics", {})
        out[r.get("campaign", {}).get("name", "")] = {
            "is": float(m.get("searchImpressionShare", 0) or 0),
            "top": float(m.get("searchTopImpressionShare", 0) or 0),
            "rank": float(m.get("searchRankLostImpressionShare", 0) or 0),
            "budget": float(m.get("searchBudgetLostImpressionShare", 0) or 0),
            "cpc": int(m.get("averageCpc", 0) or 0) / 1_000_000,
            "impr": int(m.get("impressions", 0) or 0),
        }
    return out


def pct(x: float) -> str:
    return "<10%" if abs(x - SENTINEL) < 1e-6 else f"{x * 100:.0f}%"


def rel_change(l: float, p: float) -> float | None:
    if abs(l - SENTINEL) < 1e-6 or abs(p - SENTINEL) < 1e-6 or p <= 0:
        return None
    return (l - p) / p


def short(c: str):
    """(region, label) from the campaign name."""
    s = slack.short_campaign(c)
    parts = s.split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else s)


def main() -> None:
    cur, prev = fetch(_L_START, _L_END), fetch(_P_START, _P_END)

    # order campaigns NA→EU, Brand→AEO→Competitor
    names = sorted(cur, key=lambda c: slack.campaign_group_rank(slack.short_campaign(c)))

    def cpc_rel(l, p):
        return (l - p) / p if p and p > 0 else None

    table_rows, pressure_rows, claude_in = [], [], []
    for c in names:
        a = cur[c]
        b = prev.get(c, {})
        reg, label = short(c)
        enough = bool(b) and a["impr"] >= MIN_IMPR and b.get("impr", 0) >= MIN_IMPR
        ch = rel_change(a["is"], b.get("is", 0)) if b else None
        cpc_ch = cpc_rel(a["cpc"], b.get("cpc", 0)) if b else None
        ch_str = "new" if not enough else ("n/a" if ch is None else f"{ch * 100:+.0f}%")
        cpc_str = "—" if not b or b.get("cpc", 0) <= 0 else f"{cpc_ch * 100:+.0f}%"
        table_rows.append([reg, label, pct(a["is"]), ch_str, f"${a['cpc']:,.2f}", cpc_str,
                           pct(a["rank"]), pct(a["budget"])])

        # highlight: IS swing > threshold, OR CPC jump > threshold (competitors bidding up)
        is_swing = enough and ch is not None and abs(ch) >= IS_SWING_PCT
        cpc_swing = enough and cpc_ch is not None and cpc_ch >= CPC_SWING_PCT
        if is_swing or cpc_swing:
            d_rank, d_budget = a["rank"] - b["rank"], a["budget"] - b["budget"]
            if ch is not None and ch <= -IS_SWING_PCT:
                driver = ("competitors bidding up" if d_rank >= d_budget and (cpc_ch or 0) > 0
                          else "competitors (rank ↑)" if d_rank >= d_budget else "budget cap (↑)")
            elif ch is not None and ch >= IS_SWING_PCT:
                driver = "gaining share (CPC ↑)" if (cpc_ch or 0) >= CPC_SWING_PCT else "gaining share"
            else:
                driver = "competitors bidding up"  # IS flat but CPC jumped
            pressure_rows.append([reg, label, ch_str, cpc_str, f"{d_rank * 100:+.0f}pp",
                                  f"{d_budget * 100:+.0f}pp", driver])

        claude_in.append({
            "campaign": slack.short_campaign(c), "impr_l14": a["impr"],
            "is_l14": round(a["is"], 3), "is_p14": round(b.get("is", 0), 3) if b else None,
            "is_change_pct": None if ch is None else round(ch * 100),
            "cpc_l14": round(a["cpc"], 2), "cpc_p14": round(b.get("cpc", 0), 2) if b else None,
            "cpc_change_pct": None if cpc_ch is None else round(cpc_ch * 100),
            "rank_lost_l14": round(a["rank"], 3), "rank_lost_p14": round(b.get("rank", 0), 3) if b else None,
            "budget_lost_l14": round(a["budget"], 3), "budget_lost_p14": round(b.get("budget", 0), 3) if b else None,
        })

    result = analyze_json(PROMPT, json.dumps(claude_in, separators=(",", ":")))

    blocks = [
        slack.header(f"[THURSDAY] ⚔️ Competitive Intelligence — week of {date.today():%b %d, %Y}"),
        slack.section((result.get("summary") or "Weekly impression-share review.")
                      + f"\n_Auction Insights (competitor domains) isn't in the Google Ads API — pressure is inferred from IS lost to rank vs budget. L14D = {_L_START:%b %d}–{_L_END:%b %d} vs prior 14d._"),
        slack.divider(),
        slack.section("*📊 Impression share by campaign* — L14D, change vs prior 14d"),
    ]
    blocks += slack.grouped_tables(table_rows, 0,
                                   ["Campaign", "IS", "Δ IS", "CPC", "Δ CPC", "→Rank", "→Budget"],
                                   [40, 6, 6, 8, 7, 6, 7],
                                   order_key=lambda g: (0 if g == "NA" else 1, g))

    blocks += [slack.divider(), slack.section(f"*⚔️ Competitive pressure* — IS swing > {IS_SWING_PCT * 100:.0f}% or CPC jump > {CPC_SWING_PCT * 100:.0f}% (rank↑ & CPC↑ = competitors bidding up; budget↑ = self-capped)")]
    if pressure_rows:
        blocks += slack.grouped_tables(pressure_rows, 0,
                                       ["Campaign", "Δ IS", "Δ CPC", "Δ→Rank", "Δ→Budget", "Likely driver"],
                                       [40, 6, 7, 7, 8, 24],
                                       order_key=lambda g: (0 if g == "NA" else 1, g))
    else:
        blocks.append(slack.section("No IS swings beyond the threshold this period. ✅"))

    if result.get("alerts"):
        blocks += [slack.divider(), slack.section("*🚨 Alerts*\n" + "\n".join(f"• {a}" for a in result["alerts"][:8]))]
    if result.get("recommendations"):
        blocks.append(slack.section("*🧭 Strategic recommendations*\n" + "\n".join(f"• {r}" for r in result["recommendations"][:8])))

    blocks.append(slack.context("_Competitive Intelligence · Thursdays 9am PT · agent recommends, human approves_"))
    slack.send_message(blocks, text="[THURSDAY] Competitive intelligence — impression share")


if __name__ == "__main__":
    main()
