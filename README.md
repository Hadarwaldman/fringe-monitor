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

### 15‑minute watchlist (`fringe-monitor-watchlist`)

- Trigger: EventBridge `rate(15 minutes)`
- Loads watchlist from DynamoDB
- Re-fetches programme listing, re-classifies **only watched performances**
- If status moves `sold_out`/`nearly_sold_out` → `available`, sends SES email to `notify_email` and records alert state so you are not spammed every cycle
- Also runs **show monitors** (see below) against the same programme snapshot

### Show monitors (+ optional basket hold)

A monitor targets **one show over a date range** (created on `monitors.html`).
Every 15 minutes the watchlist lambda re-classifies that show's performances in
the range and, when any becomes buyable (`available` or `nearly_sold_out`),
emails `notify_email`. Each performance alerts once per transition into
buyable; the flag resets if it sells out again.

If the monitor has **hold tickets** enabled, the lambda logs into your
edfringe account and adds the configured quantity of full-price tickets for
the earliest newly-opened performance to your basket. edfringe holds basket
items for ~30 minutes — the alert email tells you to log in and complete the
purchase before it expires. Only one performance is held per opening, and a
hold is never re-attempted on subsequent checks (no inventory hoarding).

One-time setup for holds (credentials stay only in SSM, never in the repo,
Terraform state, or DynamoDB):

```bash
AWS_PROFILE=hadar-pc aws ssm put-parameter \
  --name /fringe-monitor/edfringe-credentials \
  --type SecureString \
  --value '{"email":"you@example.com","password":"..."}'
```

Without the parameter, monitors still email — holds are skipped with a note in
the email.

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

### API (`fringe-monitor-api`)

HTTP API Gateway → Lambda.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness |
| `GET` | `/config` | Current scan window + email settings |
| `PUT` | `/config` | Update `start_date`, `end_date`, `nearly_threshold`, etc. |
| `GET` | `/watchlist` | List watched performances |
| `PUT`/`POST` | `/watchlist` | Upsert watch items (`source: manual` by default) |
| `GET` | `/monitors` | List show monitors (+ whether hold credentials are configured) |
| `POST` | `/monitors` | Create a monitor (`slug`, `show_title`, `start_date`, `end_date`, `quantity`, `hold_tickets`) |
| `PUT` | `/monitors/{id}` | Update dates/quantity/`hold_tickets`/`active` |
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
| `MONITOR` | `{monitor_id}` | Show monitor: slug, date range, quantity, `hold_tickets`, alert memory, hold results |

---

## Repo layout

```text
.
├── README.md                 ← this doc
├── scan_fringe.py            ← local CLI (uses shared lib)
├── requirements.txt
├── backend/
│   ├── fringe_lib/           ← shared scanner + AWS helpers
│   ├── lambdas/
│   │   ├── full_scan/        ← daily job
│   │   ├── watchlist/        ← 15‑min job
│   │   └── api/              ← HTTP API
│   └── requirements.txt
├── frontend/                 ← CloudFront static site
├── terraform/                ← AWS infra (S3 backend state)
├── scripts/
│   ├── package_lambda.sh     ← build build/lambda.zip
│   └── deploy.sh             ← package + apply + sync UI
└── .github/workflows/deploy.yml
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

Designed to stay cheap for festival season:

- Lambdas run on schedules (not always-on)
- DynamoDB on-demand
- S3 + CloudFront PriceClass_100
- No RDS / ECS / EC2

Typical spend should be well under a few dollars for August if traffic stays personal-use.

---

## Destroy / teardown

```bash
cd terraform
AWS_PROFILE=hadar-pc terraform destroy
```

Buckets were created with `force_destroy = true` so destroy can empty them. State bucket `fringe-monitor-tfstate-*` and optional lock table are **outside** this Terraform root and must be deleted manually if you want them gone too.
