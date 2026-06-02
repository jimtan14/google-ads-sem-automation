# Setup — enable the Slack automation

Three phases. **A & B can be done today**; C waits on the Google Ads developer token.

> Why an internal channel: `#airops-paidads-shared-main` is a **Slack Connect**
> (externally shared) channel — Slack blocks apps/webhooks from posting there.
> Automated posts must go to a regular internal channel.

## Phase A — Slack destination + webhook (~5 min)
1. Create an internal channel, e.g. `#airops-paid-ads` (a normal channel, not Connect).
2. https://api.slack.com/apps → **Create New App → From scratch** → name `AirOps Ads Bot`, workspace **teamairops**.
3. **Incoming Webhooks** → toggle **On**.
4. **Add New Webhook to Workspace** → pick `#airops-paid-ads` → **Allow**.
5. Copy the **Webhook URL** (`https://hooks.slack.com/services/T…/B…/…`).

Verify immediately (no Google Ads needed):
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"✅ AirOps ads automation — webhook test"}' <WEBHOOK_URL>
```
Or run the **Slack Smoke Test** workflow (Actions tab) after Phase B — it exercises
the real `lib/slack_client` table rendering. See `scripts/slack_smoke_test.py`.

## Phase B — GitHub Secrets (~3 min)
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value | When |
|--------|-------|------|
| `SLACK_WEBHOOK_URL` | webhook from A5 | today |
| `ANTHROPIC_API_KEY` | console.anthropic.com | today |
| `GOOGLE_ADS_CUSTOMER_ID` | `2965557823` | today |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | MCC → API Center | after token approval |
| `GOOGLE_ADS_CLIENT_ID` / `GOOGLE_ADS_CLIENT_SECRET` | GCP OAuth client | after |
| `GOOGLE_ADS_REFRESH_TOKEN` | `generate_refresh_token.py` | after |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | new MCC ID | after |

## Phase C — Google Ads access (the data gate)
See the **Step 1** section in `README.md`. Summary: create a manager (MCC) account,
link `296-555-7823`, **apply for Basic developer-token access** (1–3 business days),
create a GCP OAuth client, run `generate_refresh_token.py`, validate with
`test_credentials.py`, then paste the 6 `GOOGLE_ADS_*` secrets.

## Phase D — Verify & go live
1. Actions tab → **Slack Smoke Test → Run workflow** → confirms Slack posting works (Phase A/B only).
2. Actions tab → **Search Term Intelligence → Run workflow** → confirms the full pipeline (needs Phase C).
3. Repeat for Keyword Health and Competitive Intelligence.
4. On success, the schedules run automatically: Search Term **Mon 9am PT**,
   Keyword Health **Tue 9am PT**, Competitive **Thu 9am PT**
   (cron is UTC `0 16` = 9am PDT / 8am PST; use `0 17` for 9am during PST).
