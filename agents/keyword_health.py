"""Agent 3 — Keyword Health. Runs Tuesdays 9am PT.

Drops Quality Score (too laggy for a weekly view). Flags, per enabled keyword:
  💸 Over-average spend — L30D cost ≥ KW_SPEND_ABOVE_AVG above its ad group's
                          L30D average spend per keyword.
  📉 Very low CTR        — 7-day CTR < LOW_CTR with enough impressions.
  💰 High CPL / no Lead  — 7-day cost ≥ KW_MIN_COST with no Lead or CPL > target.

Claude then recommends pause / reduce_bid / maintain / keep_for_data per keyword,
respecting volume (low-data keywords aren't paused), match type (EXACT vs PHRASE),
and the funnel track record (any Lead/MQL/S1/S2 — esp. S2 — means keep). Posts to
#airops-paidads-shared-main. Recommends only; a human acts.
"""
import json
import os
import re
import sys
from collections import defaultdict
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
PROMPT = (ROOT / "prompts" / "keyword_health.txt").read_text()

KW_SPEND_ABOVE_AVG = float(os.environ.get("KW_SPEND_ABOVE_AVG", "0.5"))   # 50% over ad-group avg/kw
KW_SPEND_MIN_AG_COST = float(os.environ.get("KW_SPEND_MIN_AG_COST", "50"))
LOW_CTR = float(os.environ.get("LOW_CTR", "0.02"))                         # 2%
LOW_CTR_MIN_IMPR = int(os.environ.get("LOW_CTR_MIN_IMPR", "100"))
KW_MIN_COST = float(os.environ.get("KW_MIN_COST", "75"))                   # 7-day cost floor for CPL flag
CPL_TARGET = float(os.environ.get("CPL_TARGET", "300"))                    # CPL above this = "high"
FUNNEL_WINDOW_DAYS = int(os.environ.get("FUNNEL_WINDOW_DAYS", "30"))       # track-record window

_END = date.today()
_START = _END - timedelta(days=FUNNEL_WINDOW_DAYS)
FUNNEL_STAGES = {"Lead": "lead", "MQL": "mql", "S1": "s1", "S2": "s2"}

PERF_QUERY = """
SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
       metrics.clicks, metrics.impressions, metrics.ctr, metrics.cost_micros,
       campaign.name, ad_group.name, ad_group.id
FROM keyword_view
WHERE segments.date DURING LAST_7_DAYS AND ad_group_criterion.status = 'ENABLED'
  AND metrics.impressions > 0
ORDER BY metrics.cost_micros DESC
LIMIT 400
"""

SPEND_30D_QUERY = """
SELECT ad_group.id, ad_group_criterion.keyword.text,
       ad_group_criterion.keyword.match_type, metrics.cost_micros
FROM keyword_view
WHERE segments.date DURING LAST_30_DAYS AND ad_group_criterion.status = 'ENABLED'
  AND metrics.cost_micros > 0
"""


def funnel_query(where_date: str) -> str:
    return f"""
SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
       segments.conversion_action_name, metrics.all_conversions, campaign.name
FROM keyword_view
WHERE {where_date} AND metrics.all_conversions > 0
"""


def key(campaign, keyword, match) -> tuple:
    return (campaign.strip().lower(), keyword.strip().lower(), (match or "").upper())


def short_campaign(c: str) -> str:
    return re.sub(r"_[a-z]{2}_Google_[A-Z]+_Search_", " ", c or "")


def q(s: str) -> str:
    return f'"{s}"'


def fetch_funnel(where_date: str) -> dict[tuple, dict]:
    out: dict[tuple, dict] = defaultdict(lambda: {"lead": 0.0, "mql": 0.0, "s1": 0.0, "s2": 0.0})
    for r in run_query(CUSTOMER_ID, funnel_query(where_date)):
        stage = FUNNEL_STAGES.get(r.get("segments", {}).get("conversionActionName", ""))
        if not stage:
            continue
        kc = r.get("adGroupCriterion", {}).get("keyword", {})
        k = key(r.get("campaign", {}).get("name", ""), kc.get("text", ""), kc.get("matchType", ""))
        out[k][stage] += float(r.get("metrics", {}).get("allConversions", 0) or 0)
    return out


def ad_group_spend_stats(rows) -> tuple[dict, dict]:
    """Returns (kw_cost_30d by key, ag_stats by ad_group_id with total + keyword count)."""
    kw_cost: dict[tuple, float] = {}
    ag_total: dict[str, float] = defaultdict(float)
    ag_count: dict[str, int] = defaultdict(int)
    for r in rows:
        kc = r.get("adGroupCriterion", {}).get("keyword", {})
        ag_id = r.get("adGroup", {}).get("id", "")
        cost = int(r.get("metrics", {}).get("costMicros", 0) or 0) / 1_000_000
        # campaign not selected here; cost keyed by (ag_id, kw, match) is enough for share
        k = (ag_id, kc.get("text", "").strip().lower(), (kc.get("matchType") or "").upper())
        kw_cost[k] = cost
        ag_total[ag_id] += cost
        ag_count[ag_id] += 1
    ag_stats = {ag: {"total": ag_total[ag], "count": ag_count[ag],
                     "avg": ag_total[ag] / ag_count[ag] if ag_count[ag] else 0.0}
                for ag in ag_total}
    return kw_cost, ag_stats


def main() -> None:
    perf = run_query(CUSTOMER_ID, PERF_QUERY)
    kw_cost30, ag_stats = ad_group_spend_stats(run_query(CUSTOMER_ID, SPEND_30D_QUERY))
    funnel_7d = fetch_funnel("segments.date DURING LAST_7_DAYS")
    funnel_win = fetch_funnel(f"segments.date BETWEEN '{_START:%Y-%m-%d}' AND '{_END:%Y-%m-%d}'")

    over_avg, low_ctr, high_cpl, claude_in = [], [], [], []
    for r in perf:
        kc = r.get("adGroupCriterion", {}).get("keyword", {})
        m = r.get("metrics", {})
        kw, match = kc.get("text", ""), (kc.get("matchType") or "").upper()
        campaign = r.get("campaign", {}).get("name", "")
        ag_id = r.get("adGroup", {}).get("id", "")
        clicks = int(m.get("clicks", 0) or 0)
        impr = int(m.get("impressions", 0) or 0)
        ctr = float(m.get("ctr", 0) or 0)
        cost7 = int(m.get("costMicros", 0) or 0) / 1_000_000
        k = key(campaign, kw, match)
        f7 = funnel_7d.get(k, {})
        fw = funnel_win.get(k, {})
        lead7, mql7 = f7.get("lead", 0.0), f7.get("mql", 0.0)
        cost30 = kw_cost30.get((ag_id, kw.strip().lower(), match), 0.0)
        ag = ag_stats.get(ag_id, {"avg": 0.0, "count": 0, "total": 0.0})
        cpl = cost7 / lead7 if lead7 >= 0.5 else None

        flags = []
        if (ag["count"] >= 2 and ag["total"] >= KW_SPEND_MIN_AG_COST and ag["avg"] > 0
                and cost30 >= ag["avg"] * (1 + KW_SPEND_ABOVE_AVG)):
            over_avg.append([short_campaign(campaign), q(kw), match, f"${cost30:,.0f}",
                             f"${ag['avg']:,.0f}", f"+{(cost30 / ag['avg'] - 1) * 100:.0f}%",
                             round(fw.get("s2", 0), 1), k, cost30])
            flags.append("over_avg_spend")
        if ctr < LOW_CTR and impr >= LOW_CTR_MIN_IMPR:
            low_ctr.append([short_campaign(campaign), q(kw), match, impr, f"{ctr * 100:.1f}%",
                            f"${cost7:,.0f}", k, cost7])
            flags.append("low_ctr")
        if cost7 >= KW_MIN_COST and (lead7 < 0.5 or (cpl and cpl > CPL_TARGET)):
            high_cpl.append([short_campaign(campaign), q(kw), match, f"${cost7:,.0f}", clicks,
                             round(lead7, 1), round(mql7, 1),
                             f"${cpl:,.0f}" if cpl else "—", k, cost7])
            flags.append("high_cpl_no_lead")

        if flags:
            claude_in.append({
                "campaign": campaign, "keyword": kw, "match_type": match,
                "clicks": clicks, "impressions": impr, "ctr": round(ctr, 4),
                "cost_7d": round(cost7, 2), "lead_7d": round(lead7, 1), "mql_7d": round(mql7, 1),
                "lead": round(fw.get("lead", 0), 1), "mql": round(fw.get("mql", 0), 1),
                "s1": round(fw.get("s1", 0), 1), "s2": round(fw.get("s2", 0), 1),
                "spend_30d": round(cost30, 0), "ag_avg_30d": round(ag["avg"], 0), "flags": flags,
            })

    result = analyze_json(PROMPT, json.dumps(claude_in, separators=(",", ":"))) if claude_in else {}
    recs = {key(x.get("campaign", ""), x.get("keyword", ""), x.get("match_type", "")): x.get("recommendation", "review")
            for x in result.get("recommendations", [])}

    def rec(k):
        return recs.get(k, "review")

    over_avg.sort(key=lambda r: -r[-1])
    low_ctr.sort(key=lambda r: -r[-1])
    high_cpl.sort(key=lambda r: -r[-1])

    blocks = [
        slack.header(f"[TUESDAY] 🔑 Keyword Health — week of {date.today():%b %d, %Y}"),
        slack.section((result.get("summary") or "Weekly keyword-health review.") + "  _North star: S2._"),
        slack.divider(),
        slack.section(f"*💸 Spend > ad-group avg* — L30D cost ≥ {KW_SPEND_ABOVE_AVG * 100:.0f}% above the ad group's avg per keyword"),
    ]
    if over_avg:
        disp = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], rec(r[7])] for r in over_avg]
        blocks += slack.grouped_tables(disp, 0, ["Keyword", "Match", "30d $", "AG avg", "Δ", "S2", "Rec"],
                                       [34, 6, 8, 8, 5, 4, 13], order_key=slack.campaign_group_rank)
    else:
        blocks.append(slack.section("None — no keyword runs far above its ad-group average. ✅"))

    blocks += [slack.divider(), slack.section(f"*📉 Very low CTR* — 7d CTR < {LOW_CTR * 100:.0f}% with ≥ {LOW_CTR_MIN_IMPR} impressions")]
    if low_ctr:
        disp = [[r[0], r[1], r[2], r[3], r[4], r[5], rec(r[6])] for r in low_ctr]
        blocks += slack.grouped_tables(disp, 0, ["Keyword", "Match", "Impr", "CTR", "7d $", "Rec"],
                                       [38, 6, 6, 6, 7, 14], order_key=slack.campaign_group_rank)
    else:
        blocks.append(slack.section("None — all keywords above the CTR floor. ✅"))

    blocks += [slack.divider(), slack.section(f"*💰 High CPL / no Lead (7d)* — 7d cost ≥ ${KW_MIN_COST:,.0f}, no Lead or CPL > ${CPL_TARGET:,.0f}")]
    if high_cpl:
        disp = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], rec(r[8])] for r in high_cpl]
        blocks += slack.grouped_tables(disp, 0, ["Keyword", "Match", "7d $", "Clk", "Lead", "MQL", "CPL", "Rec"],
                                       [34, 6, 7, 4, 5, 5, 8, 13], order_key=slack.campaign_group_rank)
    else:
        blocks.append(slack.section("None — spend is producing leads. ✅"))

    if result.get("quick_wins"):
        blocks += [slack.divider(), slack.section("*✅ Quick wins*\n" + "\n".join(f"• {w}" for w in result["quick_wins"][:8]))]

    blocks.append(slack.context("_Keyword Health · Tuesdays 9am PT · pause/reduce/maintain/keep-for-data are suggestions; a human acts_"))
    total = len(over_avg) + len(low_ctr) + len(high_cpl)
    slack.send_message(blocks, text=f"[TUESDAY] {total} keyword-health flags this week")


if __name__ == "__main__":
    main()
