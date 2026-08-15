---
name: recommend
description: Recommend Edinburgh Fringe shows to see or monitor, using the fringe-monitor scan data, the user's PlanMyFringe schedule and wishlist, and live box-office checks. Use this whenever the user asks what to see, what to book, what to watch, or what to put a monitor on — including indirect phrasings like "I have a gap after Cathy", "what's good on Tuesday afternoon", "anything near the Pleasance before my 7pm", "what should I do with my Sunday", "find me something popular that's still available", or "what's worth monitoring for returns". Also use it when they hand you constraints (a genre, a venue, a time window, a price ceiling, a party size) and want options ranked. Prefer this skill over ad-hoc S3 or DynamoDB queries — it knows where every data source lives and how they join.
---

# /recommend

Turn a loose ask — "I have a gap after Cathy today" — into a short, specific,
checkable set of recommendations.

The hard part is not ranking. It is knowing which of five data sources answers
which part of the question, and being honest about which numbers are live and
which are hours old. `scripts/fringe_context.py` does the plumbing; your job is
the judgement on top of it.

## Workflow

**1. Pull the data.** Everything starts here:

```bash
python .claude/skills/recommend/scripts/fringe_context.py sync
```

Caches `latest.json`, `planner.json`, `history.json` (and `details.json` when
the deployment has it) into `.scratch/fringe-cache/`. Re-running is cheap — it
skips what it already has, `--force` re-downloads. Do this first; `latest.json`
is ~18 MB and you do not want to re-read it per query.

**2. Work out the actual constraint.** If the ask is time-shaped ("after
Cathy", "Tuesday afternoon"), get the real shape of their day:

```bash
python .claude/skills/recommend/scripts/fringe_context.py day 2026-08-15
```

This prints the schedule and the gaps, and deliberately computes gaps two ways
— against all commitments, and against confirmed bookings only. Those often
differ a lot, and the difference matters (see *Booked vs planned* below).

**3. Rank the field.**

```bash
python .claude/skills/recommend/scripts/fringe_context.py candidates \
  --date 2026-08-15 --from 13:05 --to 15:30 --limit 15
```

Useful flags: `--status buyable|sold_out|any`, `--rank popularity|wishlist|blend`,
`--genre`, `--venue`, `--min-pop`, `--wishlist-only`, `--include-booked`,
`--json`. Add `--json` when you need fields the table omits (box-office IDs,
offers, trend).

**4. Verify the shortlist live.** Cached availability is as-of the last scan.
Before you tell someone to go buy a ticket, re-check the two or three you are
actually recommending:

```bash
./query_show.py "after party" --date 2026-08-15
```

That hits the box office through the residential proxy and returns current
numbers. Only check the shortlist — it is one API call per performance, and the
proxy is metered.

**5. Recommend.** Short prose, not a table dump. See *Output* below.

## What the specs can be

Users hand over constraints in whatever form is natural. Map them:

| They say | You filter on |
|---|---|
| "after Cathy", "before my 7pm" | `day` subcommand → gap → `--from` / `--to` |
| "Tuesday afternoon", "tonight" | `--date` + a time window you choose |
| "near the Pleasance", "same venue" | `--venue` (venue strings are free text) |
| "comedy", "theatre", "something with music" | `--genre` (COMEDY, THEATRE, MUSICALS_AND_OPERA, …) |
| "popular", "the hot ones", "hard to get" | `--rank popularity`, optionally `--min-pop` |
| "stuff I wanted to see" | `--wishlist-only --rank wishlist` |
| "what should I monitor for returns" | `--status sold_out --rank popularity` |
| "still available", "can I still book" | `--status buyable` (the default) |
| "for two", "with the kids" | no filter exists — carry it into your prose and the live check |

When a constraint has no filter behind it (party size, price, running time),
say so rather than silently dropping it.

## The numbers, and what they actually mean

Getting these confused produces confident nonsense, so:

- **`popularity`** (`avg_percent_sold`) — how sold-out the show runs *across the
  whole date window*, averaged. This is the crowd's verdict on the show. It is
  show-level: it does not tell you about the specific performance.
- **`percent_remaining`** — seats left in *this one performance*. A show can be
  100% popular and still have a quiet Tuesday matinee.
- **`wishlist_score`** — the user's own 0–10 rating on PlanMyFringe. This is the
  only signal that is about *them*. When a show scores highly here, it usually
  beats a stranger-popular show they have never heard of. Lead with it when
  both exist.
- **`trend_per_day`** (`trend.avg_daily_sold_pct`) — average daily change in
  sold %. A high number means it is closing fast; useful for "book this today,
  not tomorrow" advice.
- **`availability`** — `sold_out` / `nearly_sold_out` / `available`, from
  `classify_availability`. Note `nearly_sold_out` also fires when the API
  reports availability level `low`, so you will occasionally see "nearly" next
  to a healthy `percent_remaining`. Trust the label over the percentage; don't
  claim a show is nearly gone on the strength of the percentage alone.

## Things that will trip you up

**Scan staleness.** `latest.json` carries `fetched_at`. The full scan runs
hourly, but a run can fail or lag. Always state how old the data is, and
live-check anything you actively recommend. On the day itself, availability
moves fast enough that an hour matters.

**Live lookups fail open.** `query_show.py` and the `/live` endpoint return
`percent_remaining: null` both for "no price data" and for "the lookup errored".
Null means *unknown*, never *available*. Say "couldn't check" rather than
guessing.

**Booked vs planned.** In `planner.json`, a schedule entry with
`confirmed: true` is a ticket they hold. `confirmed: false` is an intention —
imported as a watch item, not booked. So an unconfirmed show sitting inside a
gap is usually droppable, and the honest answer is often "your firm gap is much
bigger than your calendar looks". Show both readings when they diverge and let
the user choose; don't quietly delete their plan or quietly respect it.

**No runtimes.** The scan feed publishes no durations, and `details.json` is
frequently absent. The `day` subcommand assumes a 75-minute slot and 20 minutes
of travel (`--runtime` / `--travel` to override). Any end time you quote is an
estimate — mark it as one.

**Geography is a real constraint.** The clusters (Pleasance Courtyard/Dome,
Assembly George Square, Underbelly Cowgate/Bristo, Gilded Balloon Teviot,
Traverse, Monkey Barrel, theSpace on the Mile) are 10–25 minutes apart on foot.
A recommendation that lands them at the venue of their *next* booking is worth
more than one that is marginally more popular across town. Same-venue is the
strongest logistical win there is.

**Don't re-recommend what they have.** `candidates` already excludes every slug
on their schedule. Only pass `--include-booked` if they explicitly ask about
something they already hold.

**Sold-out means monitor, not book.** A sold-out show with high popularity is a
returns candidate. Offer to create a monitor — but creating one writes to
DynamoDB, so ask first. Same for adding watch items.

## Output

Lead with the recommendation, not the method. Aim for something a person can
act on in thirty seconds:

- **State the constraint you solved for** in one line, including any assumption
  you had to make (gap boundaries, assumed runtime, which reading of "gap").
- **Two or three picks, each with a reason** — why *this* show for *this* slot.
  The reason is usually a combination: their own wishlist score, popularity,
  and logistics. Say which performance (date + time + venue), and flag clashes.
- **Note the trade-off** when picks are mutually exclusive, and recommend one.
- **A short "also worth knowing"** — the highest-popularity options if they
  don't care about their own prior ratings, and anything worth monitoring.
- **Data freshness and any failed checks**, plainly.

Resist listing fifteen shows. The ranked table is your working material, not
the deliverable — the value you add is cutting it to the two that actually fit.

## When there's nothing good

Sometimes the honest answer is "nothing in that window is worth the walk". Say
that, and offer the adjacent options: widen the window, drop an unconfirmed
commitment, or monitor a sold-out show they'd rather see. Padding the list with
30%-popularity filler is worse than a short answer.

## Data sources, if you need to go past the script

| Source | Where | Holds |
|---|---|---|
| Latest scan | `s3://fringe-monitor-data-*/data/latest.json` | Every show + performance, availability, popularity, trend |
| Planner | `…/data/planner.json` | PlanMyFringe schedule (booked/planned) + wishlist scores |
| History | `…/data/history.json` | Daily sell-through snapshots behind `trend` |
| Show details | `…/data/details.json` | Descriptions, addresses — often absent; degrade gracefully |
| Config | DynamoDB `fringe-monitor`, `CONFIG`/`MAIN` | Active date window, `nearly_threshold`, notify email |
| Watchlist | same table, `pk=WATCHLIST` | Performances being watched for reopening |
| Monitors | same table, `pk=MONITOR` | Show monitors, incl. `hold_tickets` and hold history |
| Live availability | `./query_show.py`, or `POST /live` | Current box-office truth, via the residential proxy |

`fringe_context.py` exposes `load()`, `candidates()`, `rank()`, `gaps()`,
`config()` and `monitors()` as importable helpers — import it rather than
re-deriving a join when you need something the CLI doesn't cover.
