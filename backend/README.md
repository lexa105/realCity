# Backend

Python 3.12+ backend with Pydantic validation, SQLAlchemy storage and a FastAPI
read API. Run these commands from `backend/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.ingest path/to/bezrealitky.json
uvicorn app.main:app --reload --host 127.0.0.1
```

Save the Bezrealitky actor output as one JSON object or an array. Importing is
local and does not run Apify or incur LLM costs. `result.json` currently contains
Sreality data and requires a separate adapter; it cannot be imported yet.
The existing `test_scrape.py` is a legacy prototype; use `app.apify` for database ingestion.

Open http://127.0.0.1:8000/ for the listing page, or
http://127.0.0.1:8000/docs for interactive API documentation.
The frontend is served from `web/` with no separate build or server.

- `GET /health` checks database connectivity.
- `GET /listings?city=Praha&transaction_type=rent&max_price=20000` searches listings.
  Also supports `min_price`, `min_area`, `max_area`, and repeated `disposition`
  parameters (for example `disposition=1%2Bkk&disposition=2%2Bkk`).
  Filters apply before pagination; unknown values are excluded for active range
  filters. Reversed ranges return HTTP 422.
  Prices exclude charges; currency defaults to CZK. `limit` (1–100) and `offset`
  control pagination. Unknown prices are excluded when a price limit is supplied.
- `GET /listings/bezrealitky/1057802` retrieves a listing by provider and external ID.
  Direct lookup also returns retained deleted listings with their availability status.
- Search excludes deleted listings by default. `GET /listings?include_deleted=true`
  includes them alongside active listings, with all other filters and pagination
  still applied. The UI's **Zobrazit smazané** checkbox controls this option and
  resets pagination; clearing the filters hides deleted listings again.

## Data and database

`app/schemas.py` defines the provider payload and normalized snake_case API model.
`listingSource` is provider metadata (such as BURES), not the scraping provider.
All original fields are retained in `raw_data`; normalized data is stored in
`data` with indexed columns for filtering. Reimporting a provider/ID updates the
same row atomically; older snapshots cannot overwrite newer ones. The whole
batch is validated before any rows are written, and writes use one transaction.

Missing charges, coordinates and prices remain null. Description-based utilities
and deposit extraction is future work; no values are guessed. This structured
portal adapter does not replace the planned LLM offer/request extraction model.

SQLite is the local default. Set `DATABASE_URL` in `.env` to connect to your own
SQLite or PostgreSQL database; PostgreSQL uses `postgresql+psycopg://...`.
For Supabase or Neon, use the connection string from your database dashboard,
change its `postgresql://` prefix to `postgresql+psycopg://`, and retain the
provider's connection parameters (including SSL settings). Use a dedicated
development database first; the configured role must be able to create tables.
Set this locally in `backend/.env`; do not paste credentials into chat.
Relative SQLite paths resolve from the working directory. Without configuration,
the database is always `backend/realcity.db`. Existing environment variables take
precedence over `.env`. Keep credentials out of Git.

Tables are created on startup/import. This does not migrate existing schemas or
copy data between databases. Add versioned migrations before evolving a shared
database. PostgreSQL support requires verification against your actual instance.
The API is a local development service; add authentication and deployment
configuration before exposing it publicly. Writes are CLI-only for now.

## Apify ingestion

Set `APIFY_API_TOKEN` in `backend/.env`. From `backend/`, run:

```bash
# Starts a cloud actor run (uses your Apify account credits).
python -m app.apify --location Praha --limit 10 --max-price 20000

# Reuse saved Bezrealitky results without starting another actor run.
python -m app.apify --dataset-id YOUR_DATASET_ID --limit 100
```

The command uses the Bezrealitky actor from the prototype (`QsjkAHuaFwcSxukzl`)
for rental flats with details enabled. The default limit is 10, maximum 1000.
`--max-price` is optional; omitting it applies no price ceiling. Actor execution
has a 300-second timeout, configurable with `--timeout-seconds` (1–3600).
`--max-cost-usd` sets a separate Apify run charge limit (default: USD 0.01).
The listing limit remains in the actor input and dataset download; we do not send
Apify's `max_items`, which can translate into a budget below the startup fee for
this pay-per-event actor. The observed pricing includes a USD 0.005 startup event
and USD 0.000054 per result. Pricing can change; inspect current actor pricing
before increasing the budget. Larger jobs may need a higher explicit budget.
Dataset-only imports do not launch an actor and ignore this run budget.

Only successful actor runs are imported. Dataset retrieval completes before
validation and database writes, so failed downloads do not import partial data.
The same validation and atomic upserts used by the local JSON importer apply.
Existing dataset mode applies only the result limit; location, price, and timeout
options configure new actor runs, not saved datasets. Supply a Bezrealitky dataset;
Sreality output is not supported by this adapter.

Scraping is an explicit synchronous CLI job, separate from FastAPI startup and
HTTP requests. After import, the read API immediately sees committed listings.
This standalone import does not run availability checks. Use the daily pipeline
below to combine discovery and targeted checks. If the connection fails
after an actor starts, check Apify Console before rerunning to avoid launching
another paid run. Failed imports can be retried using the saved dataset ID.

## Daily pipeline

From `backend/`, run the complete Prague rental-flat workflow:

```bash
.venv/bin/python -m app.pipeline --max-cost-usd 0.10
```

The pipeline requests all results (`maxResults: 0`) for `Praha`, `rent`, `flat`,
with details and no price ceiling. It keeps the standalone `app.apify --limit`
command unchanged for small experiments. The default cloud budget is **$0.10 per
run**, with a 30-minute actor timeout. It never increases the budget automatically.
Daily scheduling at this cap permits up to $3.10 of actor charges in a 31-day
month; manual extra runs have their own cap.

Execution order:

1. Start the actor and immediately persist its run ID in a local JSON report.
2. Download every dataset row and validate the entire batch, scope and timestamps.
3. Import valid listings with the existing atomic upsert. Newer snapshots refresh
   availability and can reactivate previously unavailable listings.
4. Verify scrape completeness before comparing IDs. Apify `SUCCEEDED` alone is
   insufficient: even a result-limited actor run can succeed.
5. For a complete snapshot, check only absent saved IDs in the same Prague/rent/flat
   scope, excluding records observed more recently than the scrape's start time.
6. Verify each candidate directly on Bezrealitky before changing its availability.
   Missing from the dataset never directly marks a listing deleted.

Previously deleted listings absent from the scrape are checked at most once every
7 days (`--recheck-deleted-days`), while active missing listings are checked each
complete run. A returned listing does not need a redundant direct page check.
All records remain stored for analytics, and the existing UI filter works unchanged.

**Completeness verification is conservative and specific to this actor.** The
observed actor uses 15-result pages. We require consecutive pagination logs ending
in a short page, a matching final count and downloaded row count, a
normal exit, and no errors or budget/limit stops. An empty market snapshot, a final
full 15-result page without explicit exhaustion evidence, or changed logging is
treated as incomplete. Valid rows can still be imported, but all missing-listing
checks are skipped. The actor's dataset metadata count can lag after completion,
so it is not used as the coverage proof. This remains evidence about a changing
search result set, not a transactional snapshot of Bezrealitky's database. Listings
can repeat across pages as the search changes; their IDs are deduplicated and the
newest payload is kept. Reports include duplicate counts. Every absent candidate
still needs independent availability evidence before being marked deleted.

Reports are saved in ignored `backend/var/pipeline/*.json`. Status is `completed`,
`incomplete`, `partial` (unknown/stopped checks), or `failed`; CLI exit codes are
0, 2, 2, and 1 respectively. Reports include actor ID, counts, candidate IDs, and
completeness findings. A process lock prevents overlapping pipeline commands on
one host; schedule only one host per database. Completed per-listing checks remain
saved if a later request fails. There are no automatic paid retries.

If a run was started but the connection or import failed, inspect its report and
Apify Console, then reuse the cloud run rather than paying for another scrape:

```bash
.venv/bin/python -m app.pipeline --run-id APIFY_RUN_ID --max-cost-usd 0.10
```

Reuse requires the same actor, **exact** pipeline input, a successful run less
than 24 hours old, and fresh snapshot timestamps. Bare dataset IDs cannot prove
their search scope and are intentionally unsupported by the pipeline. Reusing a
run reimports idempotently and skips candidates checked since that run started.

### Daily scheduling on this Mac

```bash
# Generate and review a LaunchAgent without enabling it.
.venv/bin/python -m app.schedule_pipeline --hour 8 --minute 0 --max-cost-usd 0.10

# Install and enable it for the current logged-in macOS user.
.venv/bin/python -m app.schedule_pipeline --hour 8 --minute 0 --max-cost-usd 0.10 --install
```

The schedule runs at **08:00 in the Mac's local time zone** by default. Its label
is `cz.realcity.daily-pipeline`; installation does not start a scrape immediately.
It uses this checkout and its virtual environment, reads `backend/.env`, and writes
`backend/var/pipeline/scheduler.log` and `scheduler-error.log`. Keep the checkout
and virtual environment at the same paths. The Mac must be available with the
user session loaded and network access; this is not an always-on cloud service.
launchd handles missed calendar events during sleep on wake. There is no catch-up
loop for days when the machine was shut down.

Inspect the schedule with `launchctl print gui/$(id -u)/cz.realcity.daily-pipeline`.
Disable it with:

```bash
launchctl bootout "gui/$(id -u)/cz.realcity.daily-pipeline"
```

The plist is installed at `~/Library/LaunchAgents/cz.realcity.daily-pipeline.plist`;
remove that file as well if you want it to remain disabled after the next login.
To change the schedule, unload it, remove the old plist, and rerun the installer.
On a server, schedule the same one-shot `app.pipeline` command with cron/systemd
instead of installing the macOS launcher. Do not run ingestion inside API requests.

## Deleted listings and availability checks

Listings are never physically deleted. Their normalized and raw payloads remain
in `listings` for future analytics. The additive `listing_availability` table
stores the latest check time, deletion observation time, and reason, independently
of imported snapshots. It is created automatically on API startup or either CLI;
existing listings remain visible until checked. No existing table is rewritten.

From `backend/`, check saved listings directly on Bezrealitky:

```bash
# Preview up to 50 checks without updating listing status.
python -m app.check_availability --limit 50 --dry-run

# Persist availability for up to 50 saved listings, oldest checked first.
python -m app.check_availability --limit 50

# Check every saved listing, including previously marked deleted listings.
python -m app.check_availability --all

# Check a specific saved listing (the option can be repeated).
python -m app.check_availability --external-id 1057802
```

This does not start an Apify actor or use Apify credits. The default batch size
is 50 (`--limit` maximum 1000); `--all` removes the count limit. These options
are mutually exclusive. `--all --dry-run` previews all checks without status writes.
All mode checks the matching IDs present at the start of the job; listings imported
during the job are picked up next time. There is a one-second pause between listings and a 20-second
HTTP timeout. This standalone command runs when invoked; the daily pipeline above
uses the checker automatically for selected candidates.
Repeated batches advance through the oldest checked listings, including deleted
ones so reactivation can be detected. Dry runs do not advance the check queue.

The checker recognizes Bezrealitky's listing-not-found page (`isDetail404`),
HTTP 410, or `active: false` on the matching advert in its embedded page data.
Here, **Smazáno** means the provider reports the listing missing or inactive;
it does not establish whether it was rented, withdrawn, paused, or physically
deleted. `deleted_at` records when we first observed that state, not the actual
removal date. Repeated unavailable checks preserve this timestamp.

Timeouts, redirects away from the same listing, unrecognized markup, and provider
errors leave availability unchanged. A blocked, rate-limited, or failed network
request stops the batch. Missing a listing in a limited Apify search never marks
it deleted. If the provider changes its page structure, checks return `unknown`
until the parser is updated.

A newer confirmed active page or a newer imported snapshot reactivates a listing.
An old saved dataset cannot clear a more recent deletion observation. The API
returns `is_deleted`, `deleted_at`, `availability_checked_at`, and `deletion_reason`.
Current availability and the latest listing payload are retained; this is not
yet a full history of price changes or availability transitions.

Run integration tests (mocked Apify, real in-memory SQLite and API):

```bash
python -m unittest discover -s tests -v
```
