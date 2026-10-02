"""Daily Prague rental-flat import followed by targeted availability checks."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator
from uuid import uuid4

import httpx
from apify_client import ApifyClient
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .apify import BEZREALITKY_ACTOR_ID
from .check_availability import check_saved_listings
from .database import BACKEND_DIR, get_engine, listing_availability, listings, metadata
from .ingest import import_items
from .schemas import BezrealitkyListing


ACTOR_INPUT = {
    "location": "Praha", "transactionType": "rent", "propertyType": "flat",
    "includeDetails": True, "language": "cs", "maxResults": 0, "dispositions": [],
}
STATE_DIR = BACKEND_DIR / "var" / "pipeline"


class PipelineOptions(BaseModel):
    max_cost_usd: Decimal = Field(default=Decimal("0.10"), gt=0, allow_inf_nan=False)
    timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    recheck_deleted_days: int = Field(default=7, ge=1)


@contextmanager
def pipeline_lock(directory: Path) -> Iterator[None]:
    """Prevent overlapping scheduled/manual jobs on this host; OS releases on exit."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A pipeline is already running on this host.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def completeness_problem(log: str, items: list[BezrealitkyListing]) -> str | None:
    """Conservative adapter for the observed actor's 15-result pagination logs.

    A successful run alone is insufficient. Require contiguous full pages ending
    in a short page, matching cumulative/dataset counts, and normal actor exit.
    Exact multiples of 15 fail closed because the log has no explicit end cursor.
    """
    if not log or 'Exiting Actor ({"exit_code": 0})' not in log:
        return "Missing normal actor completion evidence."
    for line in log.splitlines():
        # The SDK emits this harmless startup warning on every cloud run.
        if "WARNING apify._configuration:" in line and "disable_browser_sandbox" in line:
            continue
        if re.search(r"\b(ERROR|WARNING|WARN|Traceback)\b", line) or re.search(
            r"requested limit|charge limit|budget|timed.out|aborted", line, re.IGNORECASE,
        ):
            return "Actor reported a warning, error, or limit; reconciliation skipped."
    if not items:
        # A zero-result market-wide snapshot is anomalous; don't fan out checks.
        return "Empty snapshot; reconciliation requires investigation."
    pages = [tuple(map(int, match)) for match in re.findall(
        r"Retrieved page (\d+) \((\d+) results, (\d+) total\)\.", log,
    )]
    total = 0
    for index, (page, count, cumulative) in enumerate(pages, start=1):
        if page != index or not 0 <= count <= 15:
            return "Unexpected pagination sequence."
        if index < len(pages) and count != 15:
            return "Pagination continued after a short page."
        total += count
        if cumulative != total:
            return "Pagination counts disagree."
    if not pages or pages[-1][1] >= 15:
        return "No short final page proving pagination exhaustion."
    endings = re.findall(r"Done! (\d+) results from (\d+) items\.", log)
    if endings != [(str(total), "1")] or total != len(items):
        return "Downloaded rows do not match the completed search log."
    return None


def candidate_ids(
    engine: Engine, seen: set[str], started_at: datetime, recheck_deleted_days: int,
) -> list[str]:
    """Only reconcile the same scope, excluding observations newer than the scrape."""
    timestamp = started_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    deleted_cutoff = (started_at - timedelta(days=recheck_deleted_days)).isoformat(timespec="microseconds")
    query = select(
        listings.c.external_id, listing_availability.c.deleted_at,
        listing_availability.c.checked_at,
    ).select_from(listings.outerjoin(listing_availability)).where(
        listings.c.source == "bezrealitky", listings.c.city == "Praha",
        listings.c.transaction_type == "rent", listings.c.data["property_type"].as_string() == "flat",
        listings.c.scraped_at <= timestamp,
    ).order_by(listings.c.external_id)
    with engine.connect() as connection:
        return [
            row.external_id for row in connection.execute(query)
            if row.external_id not in seen
            and (row.checked_at is None or row.checked_at <= timestamp)
            and (row.deleted_at is None or row.checked_at <= deleted_cutoff)
        ]


def run_pipeline(
    engine: Engine, apify: ApifyClient, http: httpx.Client, options: PipelineOptions,
    *, run_id: str | None = None, report_dir: Path = STATE_DIR,
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex}.json"
    report: dict[str, Any] = {
        "status": "starting", "actor_run_id": run_id, "input": ACTOR_INPUT,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "imported": 0, "candidates": 0, "checks": {},
        "max_cost_usd": str(options.max_cost_usd),
    }

    def save() -> None:
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)

    save()
    print(f"Report: {report_path}", flush=True)
    try:
        metadata.create_all(engine)
        if run_id is None:
            run = apify.actor(BEZREALITKY_ACTOR_ID).start(
                run_input=dict(ACTOR_INPUT), max_total_charge_usd=options.max_cost_usd,
                run_timeout=timedelta(seconds=options.timeout_seconds),
            )
            run_id = run.id
            report["actor_run_id"] = run_id
            report["status"] = "scraping"
            save()
            print(f"Apify run: {run_id}; cap ${options.max_cost_usd}", flush=True)
            run = apify.run(run_id).wait_for_finish()
        else:
            run = apify.run(run_id).get()
        if run is None or run.status != "SUCCEEDED":
            raise RuntimeError("Actor has not succeeded; no import or availability checks performed.")
        if run.act_id != BEZREALITKY_ACTOR_ID:
            raise RuntimeError("Run belongs to a different actor.")
        record = apify.key_value_store(run.default_key_value_store_id).get_record("INPUT")
        if record is None or record["value"] != ACTOR_INPUT:
            raise RuntimeError("Run input must exactly match the unlimited Prague rental-flat search.")
        now = datetime.now(timezone.utc)
        if not now - timedelta(hours=24) <= run.started_at <= now:
            raise RuntimeError("Run is older than 24 hours or has an invalid start time; start a fresh scrape.")
        report["actor_started_at"] = run.started_at.isoformat()
        report["usage_total_usd"] = str(run.usage_total_usd)
        report["status"] = "downloading"
        save()
        # No download limit. Dataset metadata counts lag immediately after a run;
        # compare actual downloaded rows with the final log instead.
        raw_items = list(apify.dataset(run.default_dataset_id).iterate_items())
        items = [BezrealitkyListing.model_validate(item) for item in raw_items]
        if any(not run.started_at - timedelta(seconds=1) <= item.scrapedAt <= now for item in items):
            raise RuntimeError("Dataset contains timestamps outside this fresh actor run.")
        if any(item.city != "Praha" or item.transactionType != "rent" or item.propertyType != "flat" for item in items):
            raise RuntimeError("Dataset contains listings outside the configured search scope.")
        log = apify.run(run_id).log().get() or ""
        problem = completeness_problem(log, items)
        actor_budget = run.options.max_total_charge_usd
        budget = min(options.max_cost_usd, Decimal(str(actor_budget))) if actor_budget is not None else options.max_cost_usd
        if run.usage_total_usd is None or Decimal(str(run.usage_total_usd)) >= budget:
            problem = "Run charge is unavailable or reached the configured budget."
        # Search results can move between pages while the actor runs. Compare
        # unique IDs and retain the newest payload for any repeated listing.
        newest: dict[str, tuple[BezrealitkyListing, dict[str, Any]]] = {}
        for item, raw in zip(items, raw_items, strict=True):
            if item.id not in newest or item.scrapedAt >= newest[item.id][0].scrapedAt:
                newest[item.id] = (item, raw)
        report["downloaded_rows"] = len(items)
        report["duplicate_rows"] = len(items) - len(newest)
        report["imported"] = import_items(engine, [raw for _, raw in newest.values()])
        report["completeness_problem"] = problem
        if problem:
            report["status"] = "incomplete"
            print(f"Imported {len(newest)} listings; {problem}", flush=True)
        else:
            missing = candidate_ids(engine, {item.id for item in items}, run.started_at, options.recheck_deleted_days)
            report["candidates"] = len(missing)
            report["candidate_ids"] = missing
            report["status"] = "checking"
            save()
            print(f"Imported {len(newest)} listings; checking {len(missing)} absent saved listings.", flush=True)
            # An empty ID list explicitly means no checks, never all listings.
            counts = check_saved_listings(engine, http, limit=None, external_ids=missing)
            report["checks"] = counts
            report["status"] = "completed" if sum(counts.values()) == len(missing) and not counts["unknown"] else "partial"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        return report
    except Exception as exc:
        # Avoid storing HTTP exception text, which may include credentials/URLs.
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, RuntimeError):
            report["error"] = str(exc)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--recheck-deleted-days", type=int, default=7)
    parser.add_argument("--run-id", help="Reuse a matching successful run from the last 24h; starts no paid run")
    args = parser.parse_args()
    options = PipelineOptions(
        max_cost_usd=args.max_cost_usd, timeout_seconds=args.timeout_seconds,
        recheck_deleted_days=args.recheck_deleted_days,
    )
    load_dotenv(BACKEND_DIR / ".env")
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        parser.error("Set APIFY_API_TOKEN in backend/.env or the environment.")
    engine = get_engine()
    try:
        with pipeline_lock(STATE_DIR), httpx.Client(timeout=20, headers={"User-Agent": "realCity-availability/1.0"}) as http:
            report = run_pipeline(engine, ApifyClient(token), http, options, run_id=args.run_id)
        print(json.dumps(report), flush=True)
        if report["status"] != "completed":
            raise SystemExit(2)
    except Exception as exc:
        print(f"Pipeline failed ({type(exc).__name__}). Inspect reports in {STATE_DIR}; reuse the actor run ID before starting another paid run.", flush=True)
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
