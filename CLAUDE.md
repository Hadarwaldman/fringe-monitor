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
    aws_util.py         boto3 helpers: DynamoDB config/watchlist, S3 writes (Lambda-only)
  lambdas/
    full_scan/handler.py  Daily job (EventBridge cron 06:00 UTC)
    watchlist/handler.py  15-min job (EventBridge rate), sends SES reopen emails
    api/handler.py        HTTP API Gateway backend (/config, /watchlist, /health)
  requirements.txt      Lambda runtime deps (httpx)
frontend/               Static CloudFront site (index.html, app.js, styles.css, config.js)
terraform/              All AWS infra; remote S3 state
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

- Full scan writes `s3://<data-bucket>/data/latest.json` (frontend source of truth), `data/config.json`, and a CSV.
- Config + watchlist live in DynamoDB table `fringe-monitor` (single-table: `CONFIG/MAIN`, `WATCHLIST/<perf_id>`, `ALERT/<perf_id>`).
- The date window is stored in DynamoDB config; change it via the UI (**Save dates**) or `PUT /config` — takes effect on the next scan.

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
