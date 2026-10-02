# How the daily pipeline works

The pipeline is one Python job that macOS starts daily. It runs a scrape in Apify, imports the results, then checks only saved listings missing from a sufficiently complete scrape. The website reads the resulting database state.

This walkthrough follows execution order and explains the meaningful lines and groups in `backend/app/pipeline.py`, the daily launcher, and the functions the pipeline calls.

## 1. macOS starts the Python process

The scheduling configuration comes from `backend/app/schedule_pipeline.py`, around lines 29–38:

```python
"ProgramArguments": [
    str(Path(sys.executable).absolute()),
    "-m", "app.pipeline",
    "--max-cost-usd", str(options.max_cost_usd),
],
"WorkingDirectory": str(BACKEND_DIR),
"StartCalendarInterval": {"Hour": args.hour, "Minute": args.minute},
```

These tell macOS:

- Which Python interpreter to run: the virtual environment's interpreter.
- Which module to execute: `app.pipeline`.
- Which arguments to pass: the spending cap.
- Which directory to start in: `backend/`.
- When to run: 08:00 local time.

Lines 34–36 configure immediate console output and log files. `RunAtLoad=False` prevents installation from immediately starting a paid scrape.

The scheduling script does not run every morning. It installs the instructions once; macOS subsequently starts `app.pipeline` directly.

## 2. Python loads the module and calls `main()`

At the bottom of `backend/app/pipeline.py`:

```python
if __name__ == "__main__":
    main()
```

Running `python -m app.pipeline` makes this module the entry point, so Python calls `main()`.

Before reaching that line, Python has processed the imports, constants, class definition, and function definitions. Defining `run_pipeline()` does not execute its body.

The imports supply four groups of capabilities:

| Imports | Purpose |
|---|---|
| `argparse`, `os`, `Path`, `json` | Command options, configuration, files and reports |
| `datetime`, `Decimal`, `uuid4`, `re` | Timestamps, decimal budget values, unique report names and log parsing |
| `contextmanager`, `fcntl` | Resource cleanup and preventing overlapping jobs |
| `httpx`, `ApifyClient`, Pydantic, SQLAlchemy, local modules | Networking, validation, database work and application functions |

## 3. Constants define what we scrape

In `backend/app/pipeline.py`, lines 29–33:

```python
ACTOR_INPUT = {
    "location": "Praha", "transactionType": "rent", "propertyType": "flat",
    "includeDetails": True, "language": "cs", "maxResults": 0,
    "dispositions": [],
}
STATE_DIR = BACKEND_DIR / "var" / "pipeline"
```

`ACTOR_INPUT` is the data sent to Apify. `maxResults=0` requests no result-count limit for this actor; the monetary budget and timeout still apply. `dispositions=[]` applies no layout restriction.

The same constant verifies that a reused run represents the same search. Comparing today's unrestricted search with yesterday's differently filtered search could create misleading missing-listing candidates.

`STATE_DIR` is where reports and the process lock live.

## 4. `main()` reads and validates configuration

`main()` defines and parses the command options:

```text
--max-cost-usd
--timeout-seconds
--recheck-deleted-days
--run-id
```

`argparse` converts command-line text into values. For example, `"1800"` becomes an integer.

The function constructs `PipelineOptions`, a Pydantic model:

```python
class PipelineOptions(BaseModel):
    max_cost_usd: Decimal = Field(default=Decimal("0.10"), gt=0, allow_inf_nan=False)
    timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    recheck_deleted_days: int = Field(default=7, ge=1)
```

Pydantic validates the configuration before work starts:

- Budget must be positive and finite.
- Timeout must be between 1 and 3,600 seconds.
- Deleted-listing recheck interval must be at least one day.

`Decimal("0.10")` represents the decimal value directly, avoiding binary floating-point approximation for the configured budget.

Next, the program loads `.env`, retrieves `APIFY_API_TOKEN`, and stops if it is missing. It creates the SQLAlchemy `Engine`, which manages database connections; it is not a permanently open transaction.

## 5. Acquire the lock and create the HTTP client

`main()` enters two context managers:

```python
with pipeline_lock(STATE_DIR), httpx.Client(
    timeout=20,
    headers={"User-Agent": "realCity-availability/1.0"},
) as http:
    report = run_pipeline(
        engine, ApifyClient(token), http, options,
        run_id=args.run_id,
    )
```

They are entered left to right: acquire the pipeline lock, then open the HTTP client, then execute the body. On exit, the client closes and the lock releases, including when the body raises an exception.

`ApifyClient(token)` communicates with Apify. `http` communicates directly with Bezrealitky when checking missing listings.

Passing the clients and engine into `run_pipeline()` is dependency injection: the function receives its dependencies instead of constructing them internally. Tests can supply mocked clients and an in-memory database.

### How the process lock works

Execution briefly enters `pipeline_lock()`:

- Create the state directory if needed.
- Open `pipeline.lock`.
- Ask the operating system for an exclusive, nonblocking lock.
- Raise a readable error if another job already holds it.
- Release the lock after `yield` hands control back and the `with` body finishes.

The `@contextmanager` decorator provides this enter/body/exit behavior around a generator function. The `yield` is not asynchronous scheduling.

The lock prevents overlapping pipeline commands on this host. It is not a distributed lock across multiple servers.

## 6. `run_pipeline()` creates a report

The function receives the database engine, Apify client, direct HTTP client, validated options, an optional existing actor run ID, and a report directory.

It ensures the report directory exists, then builds a filename containing the current UTC time and a random UUID. The initial report dictionary starts with status `starting`.

The nested `save()` function writes the report:

```python
def save() -> None:
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
```

`save()` is a closure: it accesses `report` and `report_path` from its enclosing function. Writing a temporary file and replacing the destination avoids exposing a partially written JSON report during an ordinary update.

The initial report is saved and its location printed before the external work begins.

## 7. Start a new Apify run or reuse one

The pipeline creates missing database tables using `metadata.create_all(engine)`. This creates missing tables described by our SQLAlchemy metadata; it does not migrate existing table structures.

For a new run, it starts the actor:

```python
run = apify.actor(BEZREALITKY_ACTOR_ID).start(
    run_input=dict(ACTOR_INPUT),
    max_total_charge_usd=options.max_cost_usd,
    run_timeout=timedelta(seconds=options.timeout_seconds),
)
```

It extracts the returned run ID, saves that ID and the `scraping` status immediately, prints the run ID and cap, then waits for the cloud run to finish. Saving the ID before waiting allows recovery if our local process loses its connection.

If `--run-id` was supplied, the pipeline retrieves that existing run instead. It does not start another paid actor.

These are synchronous Python calls. The actor runs independently in Apify's cloud, but the local Python process waits through the SDK. There is no `async`/`await` here, and the website continues serving requests in its separate process.

## 8. Verify the run is suitable

The pipeline checks:

1. The run exists and has status `SUCCEEDED`.
2. It belongs to the expected actor.
3. Its stored `INPUT` exactly matches `ACTOR_INPUT`.
4. It started within the last 24 hours and not in the future.

The stored input comes from the run's Apify key-value store. That store contains run configuration; the dataset contains listing records.

These checks are especially important when reusing a run. A dataset from a different city or a 50-result experiment must not drive our full-search comparison.

The pipeline records the actor's start time and reported cost, then saves the `downloading` stage.

## 9. Download and validate every listing

The pipeline consumes the dataset iterator fully:

```python
raw_items = list(apify.dataset(run.default_dataset_id).iterate_items())
items = [BezrealitkyListing.model_validate(item) for item in raw_items]
```

`iterate_items()` is an iterator: the SDK retrieves dataset pages as iteration proceeds. Wrapping it in `list(...)` consumes the complete iterator. No database import starts until the entire download succeeds.

We keep two representations:

- `raw_items`: original dictionaries for storage.
- `items`: Pydantic objects with validated fields and parsed timestamps.

The pipeline checks that each timestamp falls within the actor run and that every record is a Prague rental flat. An invalid record or wrong scope aborts before import.

The complete dataset is held in memory. That simplifies whole-batch validation at our current scale.

## 10. Determine whether the scrape appears complete

The pipeline downloads the run log and calls `completeness_problem()`. It returns either `None` (no completeness problem detected) or an explanation telling us to skip reconciliation.

The function:

| Lines | Check |
|---|---|
| 64–65 | Require the normal actor-exit message. |
| 66–73 | Scan for errors, warnings or stopping limits, except one known harmless startup warning. |
| 74–76 | Reject an empty snapshot as insufficient evidence for a market-wide comparison. |
| 77–79 | Extract page number, page size and cumulative count from each pagination log line. |
| 80–88 | Require consecutive pages, expected page sizes and consistent cumulative counts. |
| 89–90 | Require a final page containing fewer than 15 results. |
| 91–93 | Match the final log count against the actual number of downloaded rows. |
| 94 | Return `None` if all checks pass. |

For the first live run, the log showed page 71 with 15 results, then page 72 with 14 results and 1,079 total. That short final page supported that the actor exhausted its pagination.

This is conservative evidence, not an absolute guarantee. A changing website is not a transactional database snapshot. If the actor changes its logging, or ends on exactly 15 results without further evidence, the pipeline skips reconciliation.

The pipeline also rejects reconciliation when the charge is unavailable or reaches the applicable budget. When reusing a run, it uses the smaller of the configured cap and the original run's cap.

## 11. Deduplicate repeated results

Search results can move between pages while the actor runs. The pipeline builds a dictionary keyed by listing ID, so it stores one item per listing. If a listing appears twice, the later snapshot wins:

```python
newest = {}
for item, raw in zip(items, raw_items, strict=True):
    if item.id not in newest or item.scrapedAt >= newest[item.id][0].scrapedAt:
        newest[item.id] = (item, raw)
```

`zip()` pairs each validated model with its original dictionary. `strict=True` catches mismatched sequence lengths instead of silently dropping entries.

The live run had 1,079 rows but 1,078 unique listings.

## 12. Import listing data

The pipeline calls `import_items()` in `backend/app/ingest.py`:

- Validate the complete input batch.
- Choose the SQLite or PostgreSQL insert implementation.
- Start one transaction for the batch.
- Normalize provider fields into the application model.
- Build an insert containing indexed fields, normalized JSON and original JSON.
- Turn the insert into an upsert.
- Execute it.
- If the row was inserted or updated, record active availability using the snapshot timestamp.
- Return the number of validated inputs.

The upsert identifies an existing listing by `(source, external_id)` and updates it only when `incoming.scraped_at >= stored.scraped_at`.

SQLAlchemy translates that into SQL executed by the database. It is not a Python “check whether the row exists, then insert” sequence. The transaction commits when its block finishes successfully. An exception rolls back the batch, including availability updates inside that transaction.

The returned import count means records processed, not necessarily newly created records.

## 13. Decide whether to reconcile

Valid returned listings have already been imported when the pipeline examines `completeness_problem`.

```text
Valid but incomplete scrape → import returned records, skip reconciliation
Valid and complete scrape  → import records, then reconcile
```

An incomplete scrape can still contain useful new listings and price updates. It cannot safely provide the full set against which we identify missing candidates.

## 14. Select only relevant missing candidates

For a complete scrape, `candidate_ids()` receives a set of IDs returned by the actor. It calculates the scrape start time and the cutoff for rechecking deleted listings, then builds a SQLAlchemy query joining listing data with availability data.

The SQL filters restrict candidates to:

```text
Bezrealitky
Praha
Rent
Flat
Snapshot no newer than this scrape's start
```

The outer join retains listings that have no availability record yet. Constructing the query does not execute SQL; `connection.execute(query)` does.

The remaining conditions select listings whose ID was absent from the scrape, skip anything checked after this scrape started, and defer a previously deleted listing until its weekly recheck is due. Active missing listings are eligible immediately.

The result is a list of candidate IDs, not deletion decisions.

## 15. Check the selected candidates

The pipeline saves the candidate IDs and changes the report status to `checking`. It calls:

```python
counts = check_saved_listings(
    engine, http,
    limit=None,
    external_ids=missing,
)
```

`limit=None` means check the whole candidate list; `external_ids=missing` restricts checks to that list.

The distinction between `None` and `[]` matters:

- `external_ids=None`: no explicit ID restriction.
- `external_ids=[]`: explicitly no matching IDs.

That prevents “nothing is missing” from accidentally becoming “check every listing.”

The checker loads matching IDs and releases the read connection, waits one second between listings, records the request start time, requests each page, and saves each result in its own transaction. It stops the batch on blocking, rate-limit, network or server errors. The HTTP request runs outside the database write transaction, so the transaction is not held open while waiting on the website.

## 16. Decide active, deleted or unknown

`check_listing()` in `backend/app/check_availability.py` constructs the provider URL, downloads it with `httpx`, and permits redirects only to the same listing.

`classify_response()` uses BeautifulSoup to locate the embedded `__NEXT_DATA__` script, then `json.loads()` parses its contents:

| Evidence | Result |
|---|---|
| Matching advert has `active: true` | `is_deleted=False` |
| Matching advert has `active: false` | `is_deleted=True` |
| Provider's listing-not-found marker or HTTP 410 | `is_deleted=True` |
| Timeout, blocked response, unfamiliar markup | `is_deleted=None` |

Missing from the scrape only gets a listing into this check. It never directly marks it deleted.

## 17. Persist an availability observation

`record_availability()` in `backend/app/availability.py` receives the listing identity, observation timestamp, result and reason.

It normalizes the timestamp, reads the listing snapshot time, rejects an older observation, builds an availability insert, then performs an upsert only when this observation is newer than the existing availability record.

For another deleted result, `func.coalesce(existing_deleted_at, incoming_deleted_at)` keeps the existing deletion observation timestamp when one is present. For an active result, the function clears deletion fields. For an unknown result, it preserves the previous status.

The PostgreSQL `with_for_update()` locks the listing row during this transaction. SQLite uses its own write-locking behavior. Timestamp conditions separately protect against stale information arriving late.

## 18. Finish the report and exit

The pipeline saves the check counts. It marks the run `completed` only if every candidate was checked and none was unknown. Otherwise it marks the run `partial`. It saves the finish time and returns the report.

“Completed” does not mean every listing is active. It means the intended work finished with conclusive results.

On exceptions, the pipeline saves a failed report and re-raises. Most raw exception messages are omitted because network exceptions can contain sensitive URLs or credentials.

Back in `main()`:

- Print the final report.
- Exit with code 2 for incomplete or partial work.
- Exit with code 1 for an exception.
- Dispose of database resources regardless of the outcome.

Successful completion exits normally with code 0. These exit codes let a scheduler distinguish success from problems.

## 19. The website sees committed data

The pipeline does not push cards into the browser. It updates the shared database.

On the next search or page refresh, the API reads that database. Default search excludes rows with a deletion timestamp; “Zobrazit smazané” includes them. Existing browser contents are not automatically refreshed by this job.

## Execution flow

```text
macOS launchd: 08:00
        ↓
pipeline.main()
        ↓
Validate options → acquire process lock
        ↓
run_pipeline() → create report
        ↓
Start or reuse Apify run → wait
        ↓
Download all rows → validate scope and timestamps
        ↓
Check pagination evidence → deduplicate
        ↓
import_items() → commit listing snapshots
        ↓
Complete enough?
   No → report incomplete; stop
   Yes
        ↓
candidate_ids()
        ↓
Check only absent, eligible listings
        ↓
record_availability() → commit each result
        ↓
Save final report → release resources
        ↓
Next website request reads updated database
```

## Concepts to remember

- **Orchestration:** `run_pipeline()` coordinates existing functions; scraping, importing and availability decisions remain separate.
- **Evidence and freshness:** absence creates a candidate; direct evidence changes status; timestamps determine which evidence wins.
- **Transactions and idempotence:** retries update the same listings, and database writes have clear commit boundaries.
- **Process lifecycle:** macOS starts the job, the lock prevents local overlap, context managers clean up resources, and reports explain the outcome.

If it breaks, start with the JSON report in `backend/var/pipeline/`. Its stage and actor ID tell you whether to investigate the cloud run, validation/completeness checks, or direct availability requests. An `incomplete` report can be a deliberate safeguard rather than a crash.

## Check your understanding

1. Why do we import valid returned listings even when the completeness check fails?
2. Why is `external_ids=[]` different from `external_ids=None`?
3. Why must a listing checked after the scrape started be excluded from the candidate comparison?
4. If the process fails after starting Apify, how does the saved run ID help avoid unnecessary cost?
