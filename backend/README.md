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

Open http://127.0.0.1:8000/docs for interactive API documentation.

- `GET /health` checks database connectivity.
- `GET /listings?city=Praha&transaction_type=rent&max_price=20000` searches listings.
  Prices exclude charges; currency defaults to CZK. `limit` (1–100) and `offset`
  control pagination. Unknown prices are excluded when a price limit is supplied.
- `GET /listings/bezrealitky/1057802` retrieves a listing by provider and external ID.

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
No scheduler or background worker is configured yet. If the connection fails
after an actor starts, check Apify Console before rerunning to avoid launching
another paid run. Failed imports can be retried using the saved dataset ID.

Run integration tests (mocked Apify, real in-memory SQLite and API):

```bash
python -m unittest discover -s tests -v
```
