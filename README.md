# Google Ads Automation System

Eight Claude-powered agents on GitHub Actions cron schedules. Each pulls Google
Ads (+ HubSpot) data, sends it to Claude for analysis, and posts a Slack message
with action buttons. **Agents recommend; humans approve. Nothing runs autonomously.**

Target account: **AirOps** — customer ID `2965557823` (standalone, USD,
America/Los_Angeles).

---

## Step 1 — Google Ads API credentials (current step)

Produces 6 env vars (names match GitHub Secrets exactly — see `.env.example`):
`GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`,
`GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID`.

### A. Create a manager (MCC) account + link AirOps  (~10 min)
The AirOps account is standalone, so a developer token requires a new MCC.
1. <https://ads.google.com/home/tools/manager-accounts/> → **Create a manager
   account** (sign in with an `@airops.com` account) → US / USD. Save the
   10-digit **MCC ID** → `GOOGLE_ADS_LOGIN_CUSTOMER_ID`.
2. MCC → **Accounts → "+" → Link existing account** → `296-555-7823` → send.
3. AirOps account → **Admin → Access and security → Managers** → accept.

### B. Developer token — start FIRST (approval takes 1–3 business days) ⚠️
4. MCC → **Admin → API Center** → accept terms → copy token →
   `GOOGLE_ADS_DEVELOPER_TOKEN`.
5. **Apply for Basic access** in the same form. A Test-level token can only query
   *test* accounts — Basic access is required to read the live AirOps account.

### C. Google Cloud OAuth  (~10 min)
6. <https://console.cloud.google.com> → create project → **APIs & Services →
   Library → Google Ads API → Enable**.
7. **OAuth consent screen → Internal** → app name + support email → save.
8. **Credentials → Create credentials → OAuth client ID → Desktop app** → copy
   **Client ID** + **Client secret**.

### D. Refresh token  (~2 min)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python generate_refresh_token.py --client_id XXX --client_secret YYY
```
Authorize with **jim@airops.com** (must have access to both the MCC and the
AirOps account). Copy the printed token → `GOOGLE_ADS_REFRESH_TOKEN`.
> Single point of failure: the pipeline depends on jim@airops.com's Ads access.
> To harden later, re-run this as a shared ops account and swap the secret.

### E. Validate
```bash
cp .env.example .env    # fill in all 6 Google Ads values
python test_credentials.py
```
A pass prints the AirOps account name and confirms every credential works with
the exact `load_from_dict` call the agents use. (If it reports the token isn't
approved, you're still on Test access — wait for Basic.)

### F. Store in GitHub Secrets
Repo → **Settings → Secrets and variables → Actions** → add all 6 (plus
`ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`, `HUBSPOT_API_KEY` for later steps).

---

## Files
| File | Purpose |
|------|---------|
| `generate_refresh_token.py` | One-time OAuth2 flow → refresh token |
| `test_credentials.py` | Step 1 validation (same auth path as the agents) |
| `.env.example` | Credential template; copy to `.env` (git-ignored) |
| `requirements.txt` | Pinned deps |

## Next build steps
2. `lib/` shared infra (`google_ads_client.py`, `claude_client.py`,
   `slack_client.py`, `hubspot_client.py`)
3. Agent 1 (Campaign Monitor) → validates the full stack
4. Agent 8 (Pipeline Report) → validates HubSpot
5. Agents 2/3/6, then 4/5, then 7
6. Slack approval handler (serverless callback for buttons)
