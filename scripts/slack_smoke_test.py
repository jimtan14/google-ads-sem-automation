"""Slack smoke test — verify SLACK_WEBHOOK_URL posts and the aligned tables render,
WITHOUT needing Google Ads or Anthropic. Run it before the dev token lands.

Local:  export SLACK_WEBHOOK_URL=...   then  python scripts/slack_smoke_test.py
CI:     Actions → "Slack Smoke Test" → Run workflow

Only dependency: requests (+ SLACK_WEBHOOK_URL).
"""
import os
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from lib.local_env import load_dotenv
from lib import slack_client as slack

load_dotenv()


def main() -> None:
    if not os.environ.get("SLACK_WEBHOOK_URL") and os.environ.get("DRY_RUN") != "1":
        print("SLACK_WEBHOOK_URL not set. Export it (or set DRY_RUN=1 to print).")
        sys.exit(1)

    # Sample rows in the same shape the real agents produce, to exercise rendering.
    rows = [
        ["NA Brand", '"airops"', "$6.24", "+29%", "ok"],
        ["NA NonBrand_Generic_AEO", '"ai seo"', "$31.35", "+6%", "watch"],
        ["EU NonBrand_Competitors_Tier1", '"peec ai"', "$8.34", "-7%", "ok"],
    ]
    blocks = [
        slack.header(f"✅ Slack smoke test — {date.today():%b %d, %Y}"),
        slack.section("If you can read the aligned, grouped table below, the webhook and "
                      "table formatting both work. This is a test — safe to delete."),
    ]
    blocks += slack.grouped_tables(rows, 0, ["Search Term", "CPC", "Δ CPC", "Status"],
                                   [28, 8, 7, 8], order_key=slack.campaign_group_rank)
    blocks.append(slack.context("_scripts/slack_smoke_test.py · delete me_"))

    slack.send_message(blocks, text="Slack smoke test")
    print("Posted smoke test to Slack.")


if __name__ == "__main__":
    main()
