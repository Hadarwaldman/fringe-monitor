# CLAUDE.md

Guidance for working in this repo. See `README.md` for full architecture and live endpoints; this file focuses on how the code is laid out and how it ships to AWS.

## What this is

A cheap, serverless monitor for Edinburgh Festival Fringe ticket availability. It scans the whole programme daily, re-checks a watchlist every 15 minutes and emails when tickets reopen, and serves a static CloudFront UI. All infra is Terraform; everything runs in AWS `us-east-1`, account `152930225704`, profile `hadar-pc`.

## Layout

```
scan_fringe.py          Local CLI entry point (no AWS) — writes CSV/JSON to output/
backend/
  fringe_lib/           Shared scanner + AWS helpers (imported by CLI and all Lambdas)
    client.py           httpx client for the edfringe GraphQL API (anon creds embedded)
    scan.py             Programme fetch, window collection, availability classification, price enrich
    models.py           PerformanceRow dataclass
    edfest_offers.py    Fetch EdFest/2-for-1 offers, attach to performances
    trend.py            Rolling sell-through history / 7-day sold % averages
    monitors.py         Show monitors: per-show/date-range availability alerts
    planmyfringe.py     PlanMyFringe account sync: login + scrape schedule/wishlist, match to scan
    proxy.py            Loads residential-proxy URL from SSM into FRINGE_PROXY_URL (edfringe egress)
    availability.py     Cheap targeted availability: classify a specific set of box-office IDs (no programme fetch); used by monitors, wishlist-refresh, and both live endpoints (show detail, My Fringe on-load re-price)
    aws_util.py         boto3 helpers: DynamoDB config/watchlist/monitors, S3 writes (Lambda-only)
  lambdas/
    full_scan/handler.py     Daily job (EventBridge cron 06:00 UTC): full programme scan for browse-all
    monitor_check/handler.py 3-min job: lightweight show-monitor check (direct box-office-id price lookups, no programme fetch)
    wishlist_refresh/handler.py 15-min job: refresh availability for ONLY the PlanMyFringe wishlist shows (cheap per-perf lookups); replaces the old whole-programme watchlist
    watchlist/handler.py     LEGACY: whole-programme reopen scan; its EventBridge rule is DISABLED (too much proxy bandwidth). Kept for possible re-enable.
    api/handler.py           HTTP API Gateway backend (/config, /monitors, GET /shows/{slug}/availability
                             + POST /availability live lookups, /health)
  requirements.txt      Lambda runtime deps (httpx)
frontend/               Static CloudFront site. ui.js renders the shared nav (header + mobile
                        tab bar), owns user/date-window localStorage, and registers sw.js.
                        net.js (FringeNet) does all /data/*.json loading: cached-copy-first
                        from the Cache API + network revalidate with retries (weak-signal
                        resilience). sw.js is a stale-while-revalidate service worker for
                        the app shell only. app.js drives three pages via <body data-page>:
                        index.html (My Fringe itinerary), shows.html (programme browser),
                        show.html (show detail). Plus monitors.html/monitors.js and
                        settings.html/settings.js.
terraform/              All AWS infra; remote S3 state
tests/                  Offline pytest suite (fixtures + fakes; no network, no AWS) —
                        see "Testing" below
requirements-dev.txt    Test-only deps (pytest)
scripts/
  package_lambda.sh     Builds build/lambda.zip
  deploy.sh             Full local deploy: package + terraform apply + sync UI + invalidate
  dev_server.py         Offline dev server: real frontend + fixture/local-scan data + API
                        stubs + weak-network simulation (--latency/--fail-rate/--gzip)
  publish_scan.sh       Publish a local scan's output/ to the live data bucket (gzipped) —
                        fallback when the egress proxy is down
.github/workflows/deploy.yml   CI: offline tests on every PR/push; deploy on main gated on them
```

The three Lambdas share one zip (`build/lambda.zip`); each handler is copied to the zip root (`full_scan.py`, `watchlist.py`, `api.py`) alongside `fringe_lib/`.

## ⚠️ COST: proxy bandwidth is the only real expense — do not undo the cost design

**AWS is effectively free** (all within free tier). **The one metered cost is the IPRoyal residential proxy** (`FRINGE_PROXY_URL`), billed per GB. *Every* edfringe API call goes through it (required — Cloudflare 403s AWS IPs; see the `edfringe-cloudflare-datacenter-block` memory). So **proxy bandwidth = number of edfringe requests**, and that is dominated by scan breadth × frequency.

Measured bandwidth (real, Aug 2026): 1 token call ≈ 1.4 KB; 1 performance price lookup ≈ 0.46 KB; a full programme fetch ≈ 27 MB. Current monthly totals:

| Job | Cadence | Requests/run | Proxy/month |
| --- | --- | --- | --- |
| Daily full scan | 1×/day | 9 pages + ~21k prices | ~0.8 GB |
| Wishlist refresh | every 15 min | ~1,544 prices (217-show wishlist) | ~2.0 GB |
| Monitor check | every 3 min | ~6 prices | ~0.06 GB |
| Live search | on demand | ~9 prices/view | negligible |
| My Fringe load | on page load | ~1 price/upcoming entry (cap 60) | negligible |

**Total ≈ 2.8 GB/month → a 2 GB top-up lasts ~3 weeks.** Wishlist size scales this linearly (it was ~2 GB just for wishlist refresh because the wishlist is large).

**Rules to preserve the cost design — a "helpful" change here can 50× the bill:**
- **NEVER re-enable the `watchlist-15m` EventBridge rule** (it's `state = "DISABLED"` in `terraform/eventbridge.tf`). It re-fetched the whole programme every 15 min (~50+ GB/mo, ~$100+/mo). It was deliberately replaced by `wishlist_refresh` + live search.
- **NEVER add a full `fetch_all_programme` call to any frequent (sub-daily) job.** Frequent jobs must use `availability.classify_box_office_ids` (targeted per-performance lookups), never a programme scan.
- Cadence knobs live in `terraform/variables.tf`: `monitor_schedule` (3 min), `wishlist_refresh_schedule` (15 min), `daily_schedule` (06:00 UTC). To cut cost, slow `wishlist_refresh_schedule` (30 min ≈ 1.85 GB/mo, 60 min ≈ 1.35 GB/mo) — do not speed anything up without flagging the bandwidth cost.

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

## Testing (never test against Cloudflare/edfringe)

The edfringe API 403s datacenter IPs and mass-429s bulk lookups, and the live
site sits behind CloudFront — so **do not verify changes by fetching the live
site or the edfringe API**. Everything is testable offline:

```bash
pip install -r requirements-dev.txt
pytest tests/ -q                       # fringe_lib unit tests — zero network
for f in frontend/*.js; do node --check "$f"; done   # JS syntax gate

python scripts/dev_server.py           # real frontend + demo data at :8010
python scripts/dev_server.py --data output          # after a local scan
python scripts/dev_server.py --latency 3000 --fail-rate 0.4 --gzip
                                       # simulate weak signal: exercises the
                                       # cached-first / retry / offline UI
```

- Tests live in `tests/`; network behavior is covered via `httpx.MockTransport`
  and `tests/fakes.FakePricesApi` against `tests/fixtures/*.json` (a synthetic
  GraphQL programme). Extend fixtures rather than recording live responses.
- The dev server serves the production HTML/JS against fixture data and stubs
  `/api/*`, so pages can be checked with `curl http://127.0.0.1:8010/…`.
- CI runs `pytest` + `node --check` on every PR, and the deploy job on `main`
  is gated on the test job.

## Weak-network frontend rules

The scan payload is large, and the site must work on festival-venue signal:

- All `/data/*.json` loads go through `frontend/net.js` (`FringeNet.loadJson`):
  cached-copy-first from the Cache API, then network revalidate with retries
  and generous timeouts. Never `fetch` data JSON directly, never add `?ts=`
  cache-busters (S3 serves `Cache-Control: no-cache`, so revalidation is a
  cheap 304), and on failure show the saved copy + a stale banner, not a
  blank page.
- `frontend/sw.js` (stale-while-revalidate) keeps the app shell working
  offline; it deliberately does NOT touch `/data/*`. New static files must be
  added to its `SHELL` list; new pages must load `net.js` before their page
  script.
- `latest.json` performances carry only the fields the frontend uses (see
  `PerformanceRow.to_public_dict`); don't add fields without weighing payload
  size, and keep `tests/test_scan.py::test_summarize_and_payload` (the field
  contract) in sync.

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

### ⚠️ The seed S3 objects must keep `ignore_changes = all`

`aws_s3_object.seed_latest` / `seed_config` (`terraform/cloudfront.tf`) create the
first `data/latest.json` / `data/config.json` so the UI has something to read
before the first scan. **After that, the scan owns those objects and Terraform
must never touch them again.**

Do not "tighten" this to an explicit attribute list. That was the original
form, and it omitted `content_encoding`; because `put_json_s3` writes the
object gzipped, Terraform saw `content_encoding = "gzip" -> null`, planned an
in-place update, and an update re-uploads the resource's `content` — so a
routine deploy replaced a 3,050-show payload with the 296-byte "No scan yet"
placeholder and every page rendered "no match". Ignoring `content` does not
save you: one non-ignored attribute drags the placeholder content with it.

Recovery if it happens again: the data bucket has versioning enabled, so copy
the last good version back over the current one
(`aws s3api list-object-versions --prefix data/latest.json`, then
`copy_object` with the good `VersionId` and `MetadataDirective=COPY` to keep
the gzip headers).

Refresh live URLs anytime:
```bash
cd terraform && AWS_PROFILE=hadar-pc terraform output
```

## Data flow

- Full scan writes `s3://<data-bucket>/data/latest.json` (frontend source of truth), `data/details.json` (show descriptions, venue addresses, EdFest ticket links — used only by show.html; the UI degrades gracefully when it's absent), `data/config.json`, and a CSV.
- Config + watchlist + monitors live in DynamoDB table `fringe-monitor` (single-table: `CONFIG/MAIN`, `WATCHLIST/<perf_id>`, `ALERT/<perf_id>`, `MONITOR/<monitor_id>`).
- **Egress proxy (required in AWS):** the edfringe API is behind Cloudflare, which 403s AWS datacenter IPs. All Lambda→edfringe traffic must go through a residential proxy. The proxy URL (`http://user:pass@host:port`) lives ONLY in SSM SecureString `/fringe-monitor/proxy-url`; the Lambdas load it into `FRINGE_PROXY_URL` at runtime via `cart.load_proxy_into_env()`, and `client.make_async_client()` routes through it. Locally (residential IP) leave it unset → direct. Without it, the daily scan and monitors both 403. **`ProxyError: 402 Payment Required` in Lambda logs means the proxy account is out of credit** — every scheduled job fails until it's topped up. Stopgap while the proxy is down: run `python scan_fringe.py` from a residential IP, then `scripts/publish_scan.sh` to upload the fresh data (pre-gzipped, same headers as `put_json_s3`) so the site stays current.
- Monitor check cadence: the lightweight `monitor-check` Lambda runs every 3 min (`rate(3 minutes)`), checking each monitor via direct `performancePrices(box_office_id)` lookups on its stored `performances` (seeded at creation from the frontend's scan data, or self-seeded via one programme fetch on first run). No full programme fetch → cheap enough for frequent runs. The 15-min watchlist job also still checks monitors (it has the programme in hand). Both share the DynamoDB LOCK/WATCHLIST mutex so they never race.
- Show monitors (monitors.html): one show + date range; the `monitor-check` lambda emails when any performance in range becomes buyable. Notifications only — auto-hold was removed: nearly all Fringe performances use "GD" (guest/gate-door) allocation, for which the ticketing API exposes availability but NO addable price bands, so `addTickets` (the basket-hold path) has nothing to operate on. See the `edfringe-hold-gd-allocation-limitation` memory. Don't reintroduce hold without re-verifying price bands exist.
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
