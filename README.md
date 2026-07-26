# Fringe availability scanner

Scans [Edinburgh Festival Fringe](https://www.edfringe.com/tickets/whats-on) shows and writes:

1. **Raw programme snapshot** — every show with its full performance calendar  
2. **Per-performance CSV** — each date in your filter window labelled `sold_out` / `nearly_sold_out` / `available`  
3. **Per-show summary CSV** — which dates are sold out vs available for each show  

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Default filter window is **13–20 August 2026** (Edinburgh local dates). The listing fetch always pulls the **full programme**; `--start` / `--end` only filter what goes into the CSVs.

```bash
python scan_fringe.py
```

Options:

```bash
python scan_fringe.py \
  --start 2026-08-13 \
  --end 2026-08-20 \
  --nearly-threshold 20 \
  --output output/fringe_availability.csv \
  --summary-output output/fringe_show_summary.csv \
  --raw-output output/fringe_raw_programme.json
```

### How availability is decided

| Label | Meaning |
| --- | --- |
| `sold_out` | Listing `soldOut`, or ticket status `NO_ALLOCATION_CONTACT_VENUE` (Fringe allocation gone — contact venue), or remaining capacity 0% |
| `nearly_sold_out` | API availability level `low`, or remaining capacity ≤ `--nearly-threshold` (default 20%) |
| `available` | Otherwise |

## Notes

Uses the same public GraphQL API as the official tickets site. A full run usually takes a few minutes.
