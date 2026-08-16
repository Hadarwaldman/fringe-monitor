# Fringe Monitor

Cheap AWS monitor for [Edinburgh Festival Fringe](https://www.edfringe.com/tickets/whats-on) ticket availability.

It:

1. **Scans the full programme daily** and classifies each performance in a date window as `sold_out` / `nearly_sold_out` / `available`
2. **Re-checks a watchlist every 15 minutes** for tickets that reopen, and emails you
3. Serves a **CloudFront frontend** with a show table, configurable date window, and PlanMyFringe CSV compare

All AWS resources are tagged `Project=fringe-monitor` (also `Application=fringe-monitor`, `ManagedBy=terraform`).

---

## Live endpoints (current deploy)

| What | URL / name |
| --- | --- |
| Frontend | https://d25ovzu9biwv79.cloudfront.net |
| API | https://ity6awhu29.execute-api.us-east-1.amazonaws.com |
| Full-scan Lambda | `fringe-monitor-full-scan` |
| Watchlist Lambda | `fringe-monitor-watchlist` |
| API Lambda | `fringe-monitor-api` |
| DynamoDB | `fringe-monitor` |
| Data bucket | `fringe-monitor-data-20260726221516289200000002` |
| Web bucket | `fringe-monitor-web-20260726221516289200000001` |
| Notify email | `hadarwaldman@gmail.com` |
| Default window | **2026-08-12 → 2026-08-20** (editable in UI) |
| AWS account / profile | `152930225704` / `hadar-pc` |
| Region | `us-east-1` |

Refresh outputs anytime:

```bash
cd terraform && AWS_PROFILE=hadar-pc terraform output
```

---

## How it works

```text
                         edfringe GraphQL API
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          v                       v                       v
   Daily Lambda            15-min Lambda              API Lambda
   (full-scan)             (watchlist)              (config/watch)
          |                       |                       |
          |                 reopen? --> SES email         |
          |                       |                       |
          +-----------+-----------+-----------+-----------+
                      |
                      v
         S3 data/latest.json + DynamoDB
                      |
                      v
              CloudFront static UI
           (table + CSV schedule compare)
```

### Daily full scan (`fringe-monitor-full-scan`)

- Trigger: EventBridge `cron(0 6 * * ? *)` (06:00 UTC daily), or manual invoke
- Reads date window + settings from DynamoDB `CONFIG/MAIN`
- Authenticates to the public Fringe tickets API and pages the **entire programme**
- Classifies ticketed performances in the configured window (price/% remaining when needed)
- Writes:
  - `s3://…/data/latest.json` — shows + per-performance status (frontend source of truth)
  - `s3://…/data/config.json` — mirrored config for the UI
  - `s3://…/data/fringe_availability.csv` — downloadable CSV
- If `auto_watch_sold_out` is true, replaces auto watchlist entries with every performance currently `sold_out` or `nearly_sold_out` (manual watchlist rows are kept)

First production scan (12–20 Aug window): ~3007 shows, ~19.5k performances classified, ~4 minutes on a 1024 MB Lambda.

### Wishlist refresh (`fringe-monitor-wishlist-refresh`)

- Trigger: EventBridge `rate(15 minutes)`
- Refreshes availability for **only the PlanMyFringe wishlist shows** via direct per-performance price lookups (`availability.classify_box_office_ids`) — **no full programme fetch**
- Writes fresh sold-out status into `data/planner.json` so the site's wishlist view stays current
- **Replaced the old whole-programme 15-min watchlist** (`fringe-monitor-watchlist`), which re-scanned ~15,000 auto-added performances every cycle and cost ~50+ GB/month of proxy bandwidth. That lambda still exists but its EventBridge rule is **DISABLED** — do not re-enable it (see Cost notes).

### Show monitors

A monitor targets **one show over a date range** (created on `monitors.html`,
or via the **Monitor** button on any show list). A dedicated lightweight
`monitor-check` lambda runs **every 3 minutes** and, for each monitor, queries
availability for just that monitor's stored performances (no full programme
fetch — cheap enough to run frequently). When any performance becomes buyable
(`available` or `nearly_sold_out`), it emails `notify_email` with a book-now
link. Each performance alerts once per transition into buyable; the flag resets
if it sells out again.

**Notifications only — there is no auto-hold.** An earlier version tried to log
into an edfringe account and add tickets to the basket, but nearly all Fringe
performances use "GD" (guest/gate-door) allocation: the ticketing API reports
availability for these but exposes **no addable price bands**, so the basket
`addTickets` call has nothing to operate on. The reliable (and, for these
shows, only) path is the alert + manual booking via the emailed link.

### Egress proxy (required for AWS scans)

The edfringe API sits behind Cloudflare, which returns a 403 challenge to AWS
datacenter IPs. Every Lambda→edfringe request therefore routes through a
**residential proxy** (e.g. IPRoyal, pinned to Edinburgh/GB). The proxy URL is
stored only in SSM and loaded into `FRINGE_PROXY_URL` at runtime:

```bash
AWS_PROFILE=hadar-pc aws ssm put-parameter \
  --name /fringe-monitor/proxy-url \
  --type SecureString \
  --value 'http://USER:PASS@geo.iproyal.com:12321' \
  --overwrite
```

The local CLI runs from a residential IP, so it leaves `FRINGE_PROXY_URL`
unset and connects directly. Without the proxy parameter, the daily full scan
and the 15-minute watchlist/monitor checks all fail with 403.

**Failure modes to recognize in Lambda logs:**

- `403 Forbidden` on `/token` — Cloudflare is blocking the egress IP (proxy
  missing or its IPs got burned).
- `ProxyError: 402 Payment Required` — the proxy account is **out of
  credit**; every scheduled job fails until it's topped up.

While the proxy is down, keep the site's data fresh from a residential IP:
`python scan_fringe.py` then `scripts/publish_scan.sh` (uploads `output/`'s
JSON pre-gzipped with the same headers the Lambda writes). Note edfringe also
rate-limits single residential IPs hard — the CLI's default concurrency can
trip a long 429 tarpit; `--concurrency 2` and a narrower `--start/--end`
window is the reliable shape.

### API (`fringe-monitor-api`)

HTTP API Gateway → Lambda.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness |
| `GET` | `/config` | Current scan window + email settings |
| `PUT` | `/config` | Update `start_date`, `end_date`, `nearly_threshold`, etc. |
| `GET` | `/watchlist` | List watched performances |
| `PUT`/`POST` | `/watchlist` | Upsert watch items (`source: manual` by default) |
| `GET` | `/monitors` | List show monitors |
| `POST` | `/monitors` | Create a monitor (`slug`, `show_title`, `start_date`, `end_date`, `performances`) |
| `PUT` | `/monitors/{id}` | Update `start_date`/`end_date`/`active` |
| `DELETE` | `/monitors/{id}` | Remove a monitor |
| `POST` | `/monitors/check` | Run the 15-minute check immediately (async) |

CORS is open (`*`) so the CloudFront site can call it.

### Frontend

Static files in S3, served by CloudFront. Four destinations share a nav header
(desktop) / bottom tab bar (mobile), rendered by `ui.js`:

- `/` — **My Fringe** (`index.html`, `app.js`): itinerary grouped by day merging
  the PlanMyFringe schedule, local bookings and wishlist; filter chips
  (All / Booked / At risk / Wishlist); sync + CSV/PDF import.
- `/shows.html` — **Shows** browser (`app.js`): search + availability filter up
  front, the rest (genre, offers, view dates) behind a "More filters"
  disclosure; table on desktop, cards on mobile; incremental "Show more"
  rendering.
- `/show.html?slug=…` — **Show detail** (`app.js`): description, image,
  duration/age facts, venue address with a Google Maps link, buy links to both
  edfringe.com and EdFest.com, per-day availability chips, Book/Monitor.
- `/monitors.html` — show monitors page (`monitors.js`)
- `/settings.html` — active user, per-user date window, manual scan trigger,
  PlanMyFringe credentials (`settings.js`)
- `/data/latest.json` — scan results (OAC from data bucket)
- `/data/details.json` — show descriptions, venue addresses, EdFest links
- `/data/config.json` — config mirror
- `config.js` — injects `apiUrl` at deploy time
- `net.js` — shared data loader: cached-copy-first (Cache API) + background
  revalidation with retries; stale banner + Retry instead of blank pages
- `sw.js` — stale-while-revalidate service worker for the app shell, so the
  site opens instantly (and offline) on festival-venue signal

Schedule matching is a case-insensitive show-name normalize; each itinerary
card shows status and remaining % **on that day**. Booked entries (PlanMyFringe
confirmed or local bookings with price/deals) render as `✓ booked`.

### Availability labels

| Label | Meaning |
| --- | --- |
| `sold_out` | Listing `soldOut`, or ticket status `NO_ALLOCATION_CONTACT_VENUE`, or remaining capacity 0% |
| `nearly_sold_out` | API availability level `low`, or remaining capacity ≤ threshold (default **20%**) |
| `available` | Otherwise |

Same logic as the local CLI (`scan_fringe.py` / `backend/fringe_lib`).

### DynamoDB single-table layout

Table: `fringe-monitor` (pay-per-request), keys `pk` + `sk`.

| pk | sk | Contents |
| --- | --- | --- |
| `CONFIG` | `MAIN` | `start_date`, `end_date`, `nearly_threshold`, `notify_email`, `auto_watch_sold_out` |
| `WATCHLIST` | `{performance_id}` | Show/perf metadata + last known `availability` + `source` (`auto`/`manual`) |
| `ALERT` | `{performance_id}` | Dedupe for reopen emails |
| `MONITOR` | `{monitor_id}` | Show monitor: slug, date range, seeded `performances` (box-office IDs), alert memory |

---

## Repo layout

```text
.
├── README.md                 ← this doc
├── scan_fringe.py            ← local CLI (uses shared lib)
├── requirements.txt
├── requirements-dev.txt      ← test deps (pytest)
├── backend/
│   ├── fringe_lib/           ← shared scanner + AWS helpers
│   ├── lambdas/
│   │   ├── full_scan/        ← daily job
│   │   ├── watchlist/        ← 15‑min job
│   │   └── api/              ← HTTP API
│   └── requirements.txt
├── frontend/                 ← CloudFront static site
│   ├── net.js                ← cached-first /data/* loading (weak-signal)
│   └── sw.js                 ← offline app-shell service worker
├── terraform/                ← AWS infra (S3 backend state)
├── tests/                    ← offline pytest suite (fixtures, no network)
├── scripts/
│   ├── package_lambda.sh     ← build build/lambda.zip
│   ├── deploy.sh             ← package + apply + sync UI
│   ├── dev_server.py         ← offline dev server + weak-network simulator
│   └── publish_scan.sh       ← publish local scan to live bucket (proxy-down fallback)
└── .github/workflows/deploy.yml   ← tests on every PR; deploy gated on them
```

---

## Local scanner (no AWS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scan_fringe.py
# defaults: 2026-08-12 → 2026-08-20
```

Outputs under `output/`:

- `fringe_availability.csv` — per performance
- `fringe_show_summary.csv` — per show date buckets
- `fringe_raw_programme.json` — raw programme snapshot
- `latest.json` — same shape the cloud UI consumes

If the AWS proxy is down (see *Egress proxy*), a local scan can be published
straight to the live site: `scripts/publish_scan.sh` uploads `output/`'s JSON
gzipped with the same headers the Lambda uses.

---

## Tests & local dev server (fully offline)

Cloudflare blocks non-residential IPs and 429s bulk traffic, so testing never
touches edfringe or the live site:

```bash
pip install -r requirements-dev.txt
pytest tests/ -q                             # scanner/monitor/client logic
python scripts/dev_server.py                 # real frontend + demo data at :8010
python scripts/dev_server.py --data output   # browse your local scan
python scripts/dev_server.py --latency 3000 --fail-rate 0.4 --gzip  # weak-signal drill
```

The dev server serves the production frontend against fixture data (built by
running `tests/fixtures/events.json` through the real scan pipeline) and stubs
the API, so every page works with no AWS and no network. CI runs the same
tests on every PR and gates deploys on them.

The frontend itself is built for weak connections: data loads are
cached-copy-first (Cache API) with background revalidation and retries
(`frontend/net.js`), a service worker keeps the app shell available offline
(`frontend/sw.js`), and a stale-data banner with Retry appears instead of a
blank page when the network is down.

---

## Deploy

### Prerequisites

- Terraform ≥ 1.5
- AWS CLI
- Python 3.12
- `zip`
- Profile `hadar-pc` (local) or env credentials (CI)

### Local deploy

```bash
chmod +x scripts/*.sh
./scripts/deploy.sh
```

That script:

1. Builds `build/lambda.zip` (httpx + `fringe_lib` + handlers)
2. `terraform apply` against remote state
3. Writes `frontend/config.js` with the live API URL
4. `aws s3 sync` frontend → web bucket
5. CloudFront invalidation

### GitHub Actions

Workflow: `.github/workflows/deploy.yml` (push to `main` or `workflow_dispatch`).

Repo secrets:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

IAM principal needs the app services above **plus** read/write on state bucket `fringe-monitor-tfstate-152930225704`.

### Terraform state

| Item | Value |
| --- | --- |
| Backend | S3 |
| Bucket | `fringe-monitor-tfstate-152930225704` |
| Key | `fringe-monitor/terraform.tfstate` |
| Locking | S3 native `use_lockfile = true` |

Local and CI share this state — do not `terraform apply` a second empty state or you will duplicate the stack.

Variables: see `terraform/terraform.tfvars.example` (local copy `terraform.tfvars` is gitignored).

---

## Operations cheat sheet

```bash
# Outputs / URLs
cd terraform && AWS_PROFILE=hadar-pc terraform output

# Run full scan now (~few minutes)
AWS_PROFILE=hadar-pc aws lambda invoke \
  --function-name fringe-monitor-full-scan \
  --cli-read-timeout 900 \
  /tmp/fringe-full-scan.json && cat /tmp/fringe-full-scan.json

# Run watchlist check now
AWS_PROFILE=hadar-pc aws lambda invoke \
  --function-name fringe-monitor-watchlist \
  --cli-read-timeout 900 \
  /tmp/fringe-watch.json && cat /tmp/fringe-watch.json

# Tail logs
AWS_PROFILE=hadar-pc aws logs tail /aws/lambda/fringe-monitor-full-scan --follow
AWS_PROFILE=hadar-pc aws logs tail /aws/lambda/fringe-monitor-watchlist --follow

# Read live JSON
curl -s https://d25ovzu9biwv79.cloudfront.net/data/latest.json | python3 -m json.tool | head
curl -s https://ity6awhu29.execute-api.us-east-1.amazonaws.com/config
```

### Change the date window

Prefer the UI **Date window → Save dates**, or:

```bash
curl -X PUT https://ity6awhu29.execute-api.us-east-1.amazonaws.com/config \
  -H 'content-type: application/json' \
  -d '{"start_date":"2026-08-12","end_date":"2026-08-20","nearly_threshold":20}'
```

Takes effect on the next scan (invoke full-scan to refresh `latest.json` immediately).

### Email

- SES identity: `hadarwaldman@gmail.com` (must stay verified)
- Alerts only on watchlist reopen transitions
- If SES is in sandbox, only verified addresses can receive mail

---

## Cost notes

**AWS is effectively free** — schedule-driven Lambdas (not always-on), DynamoDB on-demand, S3 + CloudFront PriceClass_100, no RDS/ECS/EC2. All within the free tier; a few cents at most.

**The only real cost is the IPRoyal residential proxy** (`FRINGE_PROXY_URL`), billed **per GB**. Every edfringe API call routes through it (required — Cloudflare blocks AWS IPs), so proxy bandwidth = number of edfringe requests. Measured (Aug 2026): token ≈ 1.4 KB, one price lookup ≈ 0.46 KB, a full programme fetch ≈ 27 MB.

| Job | Cadence | Proxy/month |
| --- | --- | --- |
| Daily full scan | 1×/day | ~0.8 GB |
| Wishlist refresh (217-show wishlist) | every 15 min | ~2.0 GB |
| Monitor check | every 3 min | ~0.06 GB |
| Live search | on demand | negligible |
| **Total** | | **≈ 2.8 GB/month** |

So **a 2 GB proxy top-up lasts ~3 weeks** at current settings. Wishlist size scales the wishlist-refresh line linearly.

**To reduce proxy cost, slow the wishlist refresh** (`wishlist_refresh_schedule` in `terraform/variables.tf`): every 30 min ≈ 1.85 GB/mo (~4.7 weeks per 2 GB), every 60 min ≈ 1.35 GB/mo (~6.4 weeks).

**⚠️ Do NOT re-enable the old `watchlist-15m` rule** (deliberately `state = "DISABLED"`) or add a full-programme fetch to any frequent job — either would push proxy usage to ~50+ GB/month (~$100+). Frequent jobs must use targeted `availability.classify_box_office_ids` lookups only.

---

## Destroy / teardown

```bash
cd terraform
AWS_PROFILE=hadar-pc terraform destroy
```

Buckets were created with `force_destroy = true` so destroy can empty them. State bucket `fringe-monitor-tfstate-*` and optional lock table are **outside** this Terraform root and must be deleted manually if you want them gone too.
