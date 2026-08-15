# CLAUDE.md

Guidance for working in this repo. See `README.md` for full architecture and live endpoints; this file focuses on how the code is laid out and how it ships to AWS.

## What this is

A cheap, serverless monitor for Edinburgh Festival Fringe ticket availability. It scans the whole programme hourly, re-checks a watchlist every 15 minutes and emails when tickets reopen, and serves a static CloudFront UI. All infra is Terraform; everything runs in AWS `us-east-1`, account `152930225704`, profile `hadar-pc`.

## Layout

```
scan_fringe.py          Local CLI entry point (no AWS) — writes CSV/JSON to output/
query_show.py           Local CLI: live availability for one show (fast; --via proxy|direct|remote)
backend/
  fringe_lib/           Shared scanner + AWS helpers (imported by CLI and all Lambdas)
    client.py           httpx client for the edfringe GraphQL API (anon creds embedded)
    scan.py             Programme fetch, window collection, availability classification, price enrich
    models.py           PerformanceRow dataclass
    edfest_offers.py    Fetch EdFest/2-for-1 offers, attach to performances
    trend.py            Rolling sell-through history / 7-day sold % averages
    monitors.py         Show monitors: per-show/date-range availability alerts (+optional hold)
    planmyfringe.py     PlanMyFringe account sync: login + scrape schedule/wishlist, match to scan
    cart.py             edfringe login + add-to-basket ("hold tickets"); creds from SSM SecureString
    live.py             On-demand availability for specific performances (direct box-office-id
                        lookups, no programme fetch). Shared by query_show.py, POST /live and
                        the monitor check, so all three classify identically.
    aws_util.py         boto3 helpers: DynamoDB config/watchlist/monitors, S3 writes (Lambda-only)
  lambdas/
    full_scan/handler.py     Hourly job (EventBridge cron(0 * * * ? *))
    watchlist/handler.py     15-min job: watchlist reopen emails (full programme fetch) + monitors
    monitor_check/handler.py 3-min job: lightweight show-monitor check (direct box-office-id price lookups, no programme fetch)
    api/handler.py           HTTP API Gateway backend (/config, /watchlist, /monitors, /live, /health)
  requirements.txt      Lambda runtime deps (httpx)
frontend/               Static CloudFront site. ui.js renders the shared nav (header + mobile
                        tab bar) and owns user/date-window localStorage. app.js drives three
                        pages via <body data-page>: index.html (My Fringe itinerary),
                        shows.html (programme browser), show.html (show detail). Plus
                        monitors.html/monitors.js and settings.html/settings.js.
terraform/              All AWS infra; remote S3 state
.claude/skills/
  recommend/            /recommend skill: show recommendations from scan + planner + live checks.
                        Bundles scripts/fringe_context.py (sync/day/candidates/show) — use it
                        instead of re-deriving the S3+DynamoDB joins by hand.
scripts/
  package_lambda.sh     Builds build/lambda.zip
  deploy.sh             Full local deploy: package + terraform apply + sync UI + invalidate
.github/workflows/deploy.yml   CI deploy on push to main
```

The three Lambdas share one zip (`build/lambda.zip`); each handler is copied to the zip root (`full_scan.py`, `watchlist.py`, `api.py`) alongside `fringe_lib/`.

## Key rule: shared library

`backend/fringe_lib` is the single source of truth for scanning and classification logic. The local CLI (`scan_fringe.py`) and all Lambdas import it. When changing scan behavior or availability labels, edit `fringe_lib` — do not duplicate logic in a handler. Availability labels (`sold_out` / `nearly_sold_out` / `available`) live in `scan.classify_availability`.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scan_fringe.py          # defaults to 2026-08-12 → 2026-08-20, writes output/
```

`output/` and `.venv/` are gitignored. No AWS credentials are needed for the local CLI.

## Deploy / sync to AWS

Deploys are **not automatic on save** — they happen via the deploy script or CI. Two paths:

### Local deploy
```bash
./scripts/deploy.sh            # uses AWS_PROFILE=hadar-pc, AWS_REGION=us-east-1
```
This: (1) builds `build/lambda.zip` via `package_lambda.sh`, (2) `terraform apply` against remote state, (3) writes `frontend/config.js` with the live API URL, (4) `aws s3 sync` the frontend to the web bucket, (5) creates a CloudFront invalidation. `frontend/config.js` is **generated at deploy time** — do not hand-edit it.

### CI (GitHub Actions)
`.github/workflows/deploy.yml` has two jobs:
- **`plan`** (on `pull_request` → `main`): builds the Lambda zip, runs `terraform validate` + `terraform plan -lock=false`. Read-only — no AWS changes. This is the PR check.
- **`deploy`** (on push to `main` / merge, or manual `workflow_dispatch`): the full deploy (apply + frontend sync + invalidation).

So: **open a PR → plan runs; merge to `main` → auto-deploy.**

Auth is **GitHub OIDC — no static keys/secrets stored.** The workflow assumes IAM role `fringe-monitor-github-actions` (defined in `terraform/github_oidc.tf`), whose trust policy only allows this repo's `main` branch and same-repo PRs. The role has a service-scoped policy (S3/Lambda/CloudFront/API Gateway/DynamoDB/SES/EventBridge/Logs/IAM). `notify_email` is passed via `TF_VAR_notify_email`. CI and local share the same remote state. Note: `main` has no branch protection, so a direct push to `main` also deploys (bypassing the PR plan).

### Terraform state (important)
Remote S3 backend: bucket `fringe-monitor-tfstate-152930225704`, key `fringe-monitor/terraform.tfstate`, native S3 locking. Local and CI **share this state** — never init a second empty state or you'll duplicate the stack. `terraform/terraform.tfvars` is gitignored (copy from `terraform.tfvars.example`).

Refresh live URLs anytime:
```bash
cd terraform && AWS_PROFILE=hadar-pc terraform output
```

## Data flow

- Full scan writes `s3://<data-bucket>/data/latest.json` (frontend source of truth), `data/details.json` (show descriptions, venue addresses, EdFest ticket links — used only by show.html; the UI degrades gracefully when it's absent), `data/config.json`, and a CSV.
- Config + watchlist + monitors live in DynamoDB table `fringe-monitor` (single-table: `CONFIG/MAIN`, `WATCHLIST/<perf_id>`, `ALERT/<perf_id>`, `MONITOR/<monitor_id>`).
- **Egress proxy (required in AWS):** the edfringe API is behind Cloudflare, which 403s AWS datacenter IPs. All Lambda→edfringe traffic must go through a residential proxy. The proxy URL (`http://user:pass@host:port`) lives ONLY in SSM SecureString `/fringe-monitor/proxy-url`; the Lambdas load it into `FRINGE_PROXY_URL` at runtime via `cart.load_proxy_into_env()`, and `client.make_async_client()` routes through it. Locally (residential IP) leave it unset → direct. Without it, the daily scan and monitors both 403.
- **Full scan cadence:** hourly, `cron(0 * * * ? *)`, set by the `full_scan_schedule` Terraform variable (renamed from `daily_schedule` — drop that key from any local `terraform.tfvars` or it lingers as an undeclared-variable warning). The EventBridge rule is still named `…-daily-full-scan`; renaming it would force a resource replacement for no benefit. `trend.merge_history` keys snapshots by date and replaces same-day entries, so hourly runs refresh the day's entry rather than piling up duplicates.
- **Live lookups:** `fringe_lib.live.check_box_office_ids` is the one implementation of "what is the truth right now for these performances" — one `performancePrices` call each, bounded concurrency, no programme fetch. `POST /live`, `query_show.py` and `monitors.rows_from_box_office_ids` all route through it. It is deliberately **fail-open**: a lookup that errors leaves the row `available` with `percent_remaining=None`, so a network blip never fabricates a sell-out. Callers that need certainty must treat a null `percent_remaining` as unknown.
- Monitor check cadence: the lightweight `monitor-check` Lambda runs every 3 min (`rate(3 minutes)`), checking each monitor via direct `performancePrices(box_office_id)` lookups on its stored `performances` (seeded at creation from the frontend's scan data, or self-seeded via one programme fetch on first run). No full programme fetch → cheap enough for frequent runs. The 15-min watchlist job also still checks monitors (it has the programme in hand). Both share the DynamoDB LOCK/WATCHLIST mutex so they never race.
- Show monitors (monitors.html): one show + date range; the 15-min lambda emails when any performance in range becomes buyable, and (if `hold_tickets`) logs into the user's edfringe account and adds tickets to the basket (~30-min hold). edfringe credentials live ONLY in the SSM SecureString `/fringe-monitor/edfringe-credentials` (JSON `{"email","password"}`), created manually via `aws ssm put-parameter` — never commit them, never put them in Terraform/DynamoDB. Holds are skipped gracefully when the parameter is absent. Hold policy: one hold per monitor per opening (earliest newly-opened performance only) — never re-add on every check.
- The date window is stored in DynamoDB config; change it via the UI (**Save dates**) or `PUT /config` — takes effect on the next scan.
- **PlanMyFringe sync** (index.html "Sync calendar" button → `POST /planner/sync`): logs into the user's planmyfringe.co.uk account (classic ASP.NET Web Forms — `__VIEWSTATE` round-trip), scrapes the schedule (CalendarList) and wishlist, matches entries to the latest scan by normalized title, writes `data/planner.json` to S3, and imports unconfirmed schedule entries as watch items (`source: "planmyfringe"` — replaced wholesale each sync, manual/auto items untouched). Entries marked *confirmed* on PlanMyFringe are already-booked shows: shown with a "booked" pill, never watched. Wishlist scores render as ★ pills in the wishlist/compare/All-shows tables. Credentials (JSON `{"user_id","password"}`) live ONLY in SSM SecureString `/fringe-monitor/planmyfringe-credentials` (Lambda) or the gitignored `output/planmyfringe-creds.json` (local CLI `sync_planmyfringe.py`). Credentials can be updated from the UI: settings.html → `PUT /settings/planmyfringe` verifies with a real login then writes the SSM parameter (write-only — `GET` returns just `configured` + masked user id, the password is never sent back). Parser facts validated against the live account (Aug 2026): both the schedule and wishlist tables are on `/CalendarList` (no separate wishlist URL); dates are single-cell separator rows; "confirmed" (booked) is signalled by a `BookShow?...&remove=Y` link on the row; the site hides performances that have already started (past days need `?includepast=Y`).

## Common ops

```bash
# Invoke a scan now (~4 min)
AWS_PROFILE=hadar-pc aws lambda invoke --function-name fringe-monitor-full-scan \
  --cli-read-timeout 900 /tmp/out.json && cat /tmp/out.json

# Tail logs
AWS_PROFILE=hadar-pc aws logs tail /aws/lambda/fringe-monitor-full-scan --follow
```

## Conventions

- Python 3.12 for Lambdas (local venv is 3.10; keep new code compatible with both — code already uses `from __future__ import annotations`).
- All AWS resources tagged `Project=fringe-monitor`, `ManagedBy=terraform`.
- Keep it cheap: schedule-driven Lambdas, DynamoDB on-demand, S3 + CloudFront PriceClass_100. Don't introduce always-on infra (RDS/ECS/EC2).
- SES identity `hadarwaldman@gmail.com` must stay verified for reopen emails.
