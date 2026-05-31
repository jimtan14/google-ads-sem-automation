"""Agent 2 — Search Term Intelligence. Runs Mondays 9am PT.

North star = S2. Every judgment is made against the funnel
clicks → Lead → MQL → S1 → S2 (offline-upload conversion actions in the account),
so "quality" means how far a term's clicks actually progress toward S2.

Flag categories (posted to #airops-paidads-shared-main):
  ⚠️ Intent mismatch   — searcher intent != keyword AND landing page (Claude;
                         obvious always surfaces, borderline needs spend).
  💰 Not reaching S2    — meaningful spend + clicks over the funnel window but ~0 S2.
  💸 High CPC           — 7-day CPC >CPC_FLAG_PCT above the ad group's 30-day avg.
  🚫 Irrelevant         — off-ICP terms to negate (Claude).

Surfaces candidates only — a human reviews and acts. All spend/CPC/funnel numbers
are authoritative API figures computed in Python, never the model's echoes.
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
PROMPT = (ROOT / "prompts" / "search_term_intelligence.txt").read_text()

# Intent-mismatch gate: non-obvious mismatches need "a lot of money and clicks".
MISMATCH_MIN_COST = float(os.environ.get("MISMATCH_MIN_COST", "50"))
MISMATCH_MIN_CLICKS = int(os.environ.get("MISMATCH_MIN_CLICKS", "5"))

# High-CPC gate: 7-day term CPC vs ad group's 30-day avg CPC.
CPC_FLAG_PCT = float(os.environ.get("CPC_FLAG_PCT", "0.20"))
CPC_MIN_CLICKS = int(os.environ.get("CPC_MIN_CLICKS", "2"))
CPC_BASELINE_MIN_CLICKS = int(os.environ.get("CPC_BASELINE_MIN_CLICKS", "10"))

# Not-reaching-S2 gate (over FUNNEL_WINDOW_DAYS; window is long so S2 can mature).
FUNNEL_WINDOW_DAYS = int(os.environ.get("FUNNEL_WINDOW_DAYS", "90"))
S2_MIN_COST = float(os.environ.get("S2_MIN_COST", "250"))
S2_MIN_CLICKS = int(os.environ.get("S2_MIN_CLICKS", "15"))
S2_FLOOR = float(os.environ.get("S2_FLOOR", "0.5"))  # below this S2 count = "not reaching S2"

_END = date.today()
_START = _END - timedelta(days=FUNNEL_WINDOW_DAYS)
_FW = f"segments.date BETWEEN '{_START:%Y-%m-%d}' AND '{_END:%Y-%m-%d}'"

# Funnel conversion-action names in this account -> stage key.
FUNNEL_STAGES = {"Lead": "lead", "MQL": "mql", "S1": "s1", "S2": "s2"}

SEARCH_TERMS_QUERY = """
SELECT
  search_term_view.search_term, search_term_view.status,
  segments.keyword.info.text, segments.keyword.info.match_type,
  metrics.clicks, metrics.impressions, metrics.cost_micros, metrics.conversions,
  campaign.name, ad_group.name, ad_group.id
FROM search_term_view
WHERE segments.date DURING LAST_7_DAYS AND metrics.clicks > 0
ORDER BY metrics.cost_micros DESC
LIMIT 400
"""

LANDING_PAGE_QUERY = """
SELECT ad_group.id, ad_group_ad.ad.final_urls
FROM ad_group_ad
WHERE ad_group_ad.status = 'ENABLED' AND campaign.advertising_channel_type = 'SEARCH'
"""

CPC_BASELINE_QUERY = """
SELECT ad_group.id, metrics.cost_micros, metrics.clicks
FROM ad_group
WHERE segments.date DURING LAST_30_DAYS
  AND campaign.advertising_channel_type = 'SEARCH' AND metrics.clicks > 0
"""

FUNNEL_QUERY = f"""
SELECT search_term_view.search_term, segments.conversion_action_name,
       metrics.all_conversions, campaign.name
FROM search_term_view
WHERE {_FW} AND metrics.all_conversions > 0
"""

SPEND_QUERY = f"""
SELECT search_term_view.search_term, metrics.clicks, metrics.cost_micros, campaign.name
FROM search_term_view
WHERE {_FW} AND metrics.clicks > 0
ORDER BY metrics.cost_micros DESC
LIMIT 500
"""


def key(campaign: str, term: str) -> tuple:
    return (campaign.strip().lower(), term.strip().lower())


def short_campaign(c: str) -> str:
    """NA_en_Google_BOF_Search_NonBrand_Generic_AEO -> 'NA NonBrand_Generic_AEO'."""
    return re.sub(r"_[a-z]{2}_Google_[A-Z]+_Search_", " ", c or "")


def q(term: str) -> str:
    return f'"{term}"'


def landing_pages() -> dict[str, str]:
    pages: dict[str, str] = {}
    for r in run_query(CUSTOMER_ID, LANDING_PAGE_QUERY):
        ag_id = r.get("adGroup", {}).get("id")
        urls = r.get("adGroupAd", {}).get("ad", {}).get("finalUrls") or []
        if ag_id and urls and ag_id not in pages:
            pages[ag_id] = urls[0].split("?", 1)[0]
    return pages


def cpc_baselines() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in run_query(CUSTOMER_ID, CPC_BASELINE_QUERY):
        ag_id = r.get("adGroup", {}).get("id")
        clicks = int(r.get("metrics", {}).get("clicks", 0) or 0)
        cost = int(r.get("metrics", {}).get("costMicros", 0) or 0) / 1_000_000
        if ag_id and clicks:
            out[ag_id] = {"cpc": cost / clicks, "clicks": clicks}
    return out


def funnel_by_term() -> dict[tuple, dict]:
    """(campaign, term) -> {lead, mql, s1, s2} over the funnel window."""
    out: dict[tuple, dict] = defaultdict(lambda: {"lead": 0.0, "mql": 0.0, "s1": 0.0, "s2": 0.0})
    for r in run_query(CUSTOMER_ID, FUNNEL_QUERY):
        stage = FUNNEL_STAGES.get(r.get("segments", {}).get("conversionActionName", ""))
        if not stage:
            continue
        k = key(r.get("campaign", {}).get("name", ""), r.get("searchTermView", {}).get("searchTerm", ""))
        out[k][stage] += float(r.get("metrics", {}).get("allConversions", 0) or 0)
    return out


def spend_by_term() -> dict[tuple, dict]:
    """(campaign, term) -> {campaign, term, clicks, cost} over the funnel window."""
    out: dict[tuple, dict] = {}
    for r in run_query(CUSTOMER_ID, SPEND_QUERY):
        term = r.get("searchTermView", {}).get("searchTerm", "")
        camp = r.get("campaign", {}).get("name", "")
        out[key(camp, term)] = {
            "campaign": camp, "term": term,
            "clicks": int(r.get("metrics", {}).get("clicks", 0) or 0),
            "cost": int(r.get("metrics", {}).get("costMicros", 0) or 0) / 1_000_000,
        }
    return out


def build_dataset(rows, pages, funnel) -> tuple[list[dict], dict]:
    """Flatten 7-day rows (micros->USD, attach landing page + funnel track record)
    and build an authoritative (campaign, term) -> metrics index."""
    data: list[dict] = []
    agg: dict[tuple, dict] = defaultdict(
        lambda: {"clicks": 0, "impressions": 0, "cost": 0.0, "keyword": "",
                 "landing_page": "", "ad_group_id": "", "campaign": "", "term": ""}
    )
    for r in rows:
        stv, m = r.get("searchTermView", {}), r.get("metrics", {})
        kw = r.get("segments", {}).get("keyword", {}).get("info", {})
        ag_id = r.get("adGroup", {}).get("id", "")
        term = stv.get("searchTerm", "")
        campaign = r.get("campaign", {}).get("name", "")
        f = funnel.get(key(campaign, term), {})
        data.append({
            "term": term, "status": stv.get("status", ""),
            "keyword": kw.get("text", ""), "match_type": kw.get("matchType", ""),
            "landing_page": pages.get(ag_id, ""), "campaign": campaign,
            "ad_group": r.get("adGroup", {}).get("name", ""),
            "clicks": int(m.get("clicks", 0) or 0),
            "impressions": int(m.get("impressions", 0) or 0),
            "cost": round(int(m.get("costMicros", 0) or 0) / 1_000_000, 2),
            # trailing-window funnel = the term's quality track record
            "lead": round(f.get("lead", 0), 1), "mql": round(f.get("mql", 0), 1),
            "s1": round(f.get("s1", 0), 1), "s2": round(f.get("s2", 0), 1),
        })
        a = agg[key(campaign, term)]
        a["clicks"] += int(m.get("clicks", 0) or 0)
        a["impressions"] += int(m.get("impressions", 0) or 0)
        a["cost"] += int(m.get("costMicros", 0) or 0) / 1_000_000
        a["keyword"] = a["keyword"] or kw.get("text", "")
        a["landing_page"] = a["landing_page"] or pages.get(ag_id, "")
        a["ad_group_id"] = a["ad_group_id"] or ag_id
        a["campaign"] = a["campaign"] or campaign
        a["term"] = a["term"] or term
    return data, agg


def cpc_flags(agg, baselines) -> list[list]:
    rows = []
    for a in agg.values():
        if a["clicks"] < CPC_MIN_CLICKS or a["cost"] <= 0:
            continue
        base = baselines.get(a["ad_group_id"])
        if not base or base["clicks"] < CPC_BASELINE_MIN_CLICKS:
            continue
        term_cpc = a["cost"] / a["clicks"]
        delta = (term_cpc - base["cpc"]) / base["cpc"]
        if delta <= CPC_FLAG_PCT:
            continue
        extra = (term_cpc - base["cpc"]) * a["clicks"]
        rows.append([short_campaign(a["campaign"]), q(a["term"]), a["clicks"],
                     f"${term_cpc:,.2f}", f"${base['cpc']:,.2f}", f"+{delta * 100:.0f}%",
                     f"${extra:,.0f}", extra])
    rows.sort(key=lambda r: -r[-1])
    return [r[:-1] for r in rows]


def s2_flags(spend, funnel) -> list[list]:
    """Terms with real spend + clicks over the window but ~0 S2."""
    rows = []
    for k, sp in spend.items():
        if sp["cost"] < S2_MIN_COST or sp["clicks"] < S2_MIN_CLICKS:
            continue
        f = funnel.get(k, {})
        if f.get("s2", 0) >= S2_FLOOR:
            continue
        rows.append([short_campaign(sp["campaign"]), q(sp["term"]), sp["clicks"],
                     f"${sp['cost']:,.0f}", round(f.get("lead", 0), 1), round(f.get("mql", 0), 1),
                     round(f.get("s1", 0), 1), round(f.get("s2", 0), 1), sp["cost"]])
    rows.sort(key=lambda r: -r[-1])
    return [r[:-1] for r in rows]


def pipe_table(headers, rows, widths) -> list[str]:
    header_line = " | ".join(headers)
    body = [" | ".join(str(c)[:w] for c, w in zip(r, widths)) for r in rows]
    chunks, cur, cur_len = [], [header_line], len(header_line)
    for line in body:
        if cur_len + len(line) + 1 > 2700 and len(cur) > 1:
            chunks.append("```\n" + "\n".join(cur) + "\n```")
            cur, cur_len = [header_line], len(header_line)
        cur.append(line)
        cur_len += len(line) + 1
    if len(cur) > 1:
        chunks.append("```\n" + "\n".join(cur) + "\n```")
    return chunks


def main() -> None:
    funnel = funnel_by_term()
    rows = run_query(CUSTOMER_ID, SEARCH_TERMS_QUERY)
    data, agg = build_dataset(rows, landing_pages(), funnel)
    baselines = cpc_baselines()
    spend = spend_by_term()
    result = analyze_json(PROMPT, json.dumps(data, separators=(",", ":")))

    # Intent mismatch — obvious always; non-obvious needs spend+clicks.
    mismatch_rows = []
    for mm in result.get("intent_mismatch", []):
        a = agg.get(key(mm.get("campaign", ""), mm.get("term", "")))
        if not a:
            continue
        obvious = mm.get("obvious") is True
        if not (obvious or (a["cost"] >= MISMATCH_MIN_COST and a["clicks"] >= MISMATCH_MIN_CLICKS)):
            continue
        reason, action = mm.get("reason", ""), mm.get("suggested_action", "")
        f = funnel.get(key(a["campaign"], a["term"]), {})
        mismatch_rows.append([("❗ " if obvious else "") + short_campaign(a["campaign"]), q(a["term"]),
                              f"${a['cost']:,.0f}", a["clicks"], round(f.get("s2", 0), 1),
                              f"{reason} → {action}" if action else reason, obvious, a["cost"]])
    mismatch_rows.sort(key=lambda r: (not r[-2], -r[-1]))
    mismatch_rows = [r[:-2] for r in mismatch_rows]

    cpc_rows = cpc_flags(agg, baselines)
    s2_rows = s2_flags(spend, funnel)

    negate_rows = []
    for n in result.get("negate", []):
        a = agg.get(key(n.get("campaign", ""), n.get("term", ""))) or {}
        negate_rows.append([short_campaign(n.get("campaign", "")), q(n.get("term", "")),
                            a.get("impressions", 0), a.get("clicks", 0), n.get("reason", "")])
    negate_rows.sort(key=lambda r: (r[0], -int(r[2] or 0)))

    blocks = [
        slack.header(f"[MONDAY] 🔎 Search Term Intelligence — week of {date.today():%b %d, %Y}"),
        slack.section((result.get("summary") or "Weekly search-term review.") + "  _North star: S2._"),
        slack.divider(),
        slack.section(f"*⚠️ Intent mismatch* — wrong-intent clicks (❗ = obvious; else ≥ ${MISMATCH_MIN_COST:,.0f} & ≥ {MISMATCH_MIN_CLICKS} clk)"),
    ]
    if mismatch_rows:
        for tbl in pipe_table(["Campaign", "Search Term", "Cost", "Clk", "S2", "Why / action"],
                              mismatch_rows, [22, 42, 7, 4, 4, 72]):
            blocks.append(slack.section(tbl))
    else:
        blocks.append(slack.section("None this week — high-spend terms land on aligned pages. ✅"))

    blocks += [slack.divider(),
               slack.section(f"*💰 Spend not reaching S2* — last {FUNNEL_WINDOW_DAYS}d, ≥ ${S2_MIN_COST:,.0f} & ≥ {S2_MIN_CLICKS} clk, S2 < {S2_FLOOR}")]
    if s2_rows:
        for tbl in pipe_table(["Campaign", "Search Term", "Clk", "Cost", "Lead", "MQL", "S1", "S2"],
                              s2_rows, [22, 34, 5, 9, 6, 6, 5, 5]):
            blocks.append(slack.section(tbl))
        blocks.append(slack.context("_S2 lags — competitor/category terms may still be early-funnel. Review, don't auto-cut._"))
    else:
        blocks.append(slack.section("None — all material spend is producing S2. ✅"))

    blocks += [slack.divider(),
               slack.section(f"*💸 High CPC* — > {CPC_FLAG_PCT * 100:.0f}% above ad group's 30-day avg CPC (≥ {CPC_MIN_CLICKS} clk)")]
    if cpc_rows:
        for tbl in pipe_table(["Campaign", "Search Term", "Clk", "CPC", "AG 30d", "Δ", "Extra $"],
                              cpc_rows, [22, 34, 4, 9, 9, 6, 9]):
            blocks.append(slack.section(tbl))
    else:
        blocks.append(slack.section("None — CPCs within 20% of ad-group norms. ✅"))

    blocks += [slack.divider(), slack.section("*🚫 Irrelevant — add as negatives*")]
    if negate_rows:
        for tbl in pipe_table(["Campaign", "Search Term", "Impr", "Clk", "Reason"],
                              negate_rows, [22, 40, 5, 4, 80]):
            blocks.append(slack.section(tbl))
    else:
        blocks.append(slack.section("No clearly irrelevant terms this week. 🎉"))

    blocks.append(slack.context("_Search Term Intelligence · Mondays 9am PT · agent recommends, human approves_"))
    total = len(mismatch_rows) + len(s2_rows) + len(cpc_rows) + len(negate_rows)
    slack.send_message(blocks, text=f"[MONDAY] {total} search-term flags this week")


if __name__ == "__main__":
    main()
