import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.apify import BEZREALITKY_ACTOR_ID
from app.availability import record_availability
from app.check_availability import check_saved_listings
from app.database import metadata, listings
from app.ingest import import_items
from app.pipeline import ACTOR_INPUT, PipelineOptions, candidate_ids, completeness_problem, pipeline_lock, run_pipeline
from app.schemas import BezrealitkyListing


def completion_log(count: int) -> str:
    lines = []
    total = 0
    for page, offset in enumerate(range(0, count, 15), 1):
        size = min(15, count - offset)
        total += size
        lines.append(f"[apify] INFO  Retrieved page {page} ({size} results, {total} total).")
    return "\n".join(lines + [f"[apify] INFO  Done! {count} results from 1 items.", '[apify] INFO  Exiting Actor ({"exit_code": 0})'])


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        self.started = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.base = dict(
            id="1", url="https://www.bezrealitky.cz/nemovitosti-byty-domy/1", title="Byt",
            transactionType="rent", propertyType="flat", city="Praha", currency="CZK",
            scrapedAt=(self.started + timedelta(minutes=1)).isoformat(),
        )
        self.apify = MagicMock()
        self.run = SimpleNamespace(
            id="run-1", act_id=BEZREALITKY_ACTOR_ID, status="SUCCEEDED",
            started_at=self.started, default_dataset_id="dataset-1",
            default_key_value_store_id="store-1", usage_total_usd=0.005,
            options=SimpleNamespace(max_total_charge_usd=0.10),
        )
        self.apify.actor.return_value.start.return_value = self.run
        self.apify.run.return_value.wait_for_finish.return_value = self.run
        self.apify.run.return_value.get.return_value = self.run
        self.apify.key_value_store.return_value.get_record.return_value = {"value": dict(ACTOR_INPUT)}
        self.apify.dataset.return_value.iterate_items.return_value = [self.base]
        self.apify.run.return_value.log.return_value.get.return_value = completion_log(1)
        self.http = self.enterContext(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(410))))

    def execute(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_pipeline(self.engine, self.apify, self.http, PipelineOptions(), report_dir=self.directory, **kwargs)

    def seed(self, identifier: str, **overrides) -> None:
        import_items(self.engine, [{**self.base, "id": identifier, "scrapedAt": (self.started - timedelta(days=8)).isoformat(), **overrides}])

    def test_end_to_end_only_missing_same_scope_is_checked(self) -> None:
        self.seed("1")
        self.seed("2")
        self.seed("3", city="Brno")
        self.seed("4", transactionType="sale")
        self.seed("5", propertyType="house")
        self.seed("6", scrapedAt=(self.started + timedelta(minutes=2)).isoformat())
        with patch("app.check_availability.time.sleep"):
            report = self.execute()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["candidate_ids"], ["2"])
        self.assertEqual(report["checks"]["deleted"], 1)
        self.apify.dataset.return_value.iterate_items.assert_called_once_with()
        args = self.apify.actor.return_value.start.call_args.kwargs
        self.assertEqual(args["run_input"]["maxResults"], 0)
        self.assertEqual(args["max_total_charge_usd"], Decimal("0.10"))
        self.assertEqual(json.loads(next(self.directory.glob("*.json")).read_text())["status"], "completed")

    def test_no_missing_ids_makes_no_http_requests(self) -> None:
        with patch("app.check_availability.check_listing") as check:
            report = self.execute()
        check.assert_not_called()
        self.assertEqual(report["candidates"], 0)

    def test_recently_deleted_is_skipped_but_week_old_deleted_is_checked(self) -> None:
        for identifier in ["2", "3"]:
            self.seed(identifier)
        with self.engine.begin() as c:
            record_availability(c, "bezrealitky", "2", self.started - timedelta(hours=1), is_deleted=True)
            record_availability(c, "bezrealitky", "3", self.started - timedelta(days=7), is_deleted=True)
        self.assertEqual(candidate_ids(self.engine, set(), self.started, 7), ["3"])

    def test_partial_scrape_imports_but_never_reconciles(self) -> None:
        self.seed("2")
        self.apify.run.return_value.log.return_value.get.return_value += "\nReached the requested limit of 1 results."
        with patch("app.pipeline.check_saved_listings") as check:
            report = self.execute()
        check.assert_not_called()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["imported"], 1)

    def test_budget_exhaustion_disables_reconciliation(self) -> None:
        self.run.usage_total_usd = 0.10
        with patch("app.pipeline.check_saved_listings") as check:
            self.assertEqual(self.execute()["status"], "incomplete")
        check.assert_not_called()

    def test_reusing_run_starts_no_paid_actor(self) -> None:
        self.execute(run_id="run-1")
        self.apify.actor.assert_not_called()

    def test_failed_old_wrong_scope_or_invalid_runs_never_import(self) -> None:
        scenarios = ["failed", "old", "scope", "actor", "invalid", "timestamp", "wrong_city"]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                self.run.status = "FAILED" if scenario == "failed" else "SUCCEEDED"
                self.run.started_at = self.started - timedelta(days=2) if scenario == "old" else self.started
                self.run.act_id = "other" if scenario == "actor" else BEZREALITKY_ACTOR_ID
                self.apify.key_value_store.return_value.get_record.return_value = {"value": {**ACTOR_INPUT, "maxResults": 50} if scenario == "scope" else dict(ACTOR_INPUT)}
                item = dict(self.base)
                if scenario == "invalid": item["price"] = -1
                if scenario == "timestamp": item["scrapedAt"] = (self.started - timedelta(days=1)).isoformat()
                if scenario == "wrong_city": item["city"] = "Brno"
                self.apify.dataset.return_value.iterate_items.return_value = [item]
                with self.assertRaises(Exception), patch("app.pipeline.check_saved_listings") as check:
                    self.execute()
                check.assert_not_called()
                with self.engine.connect() as c:
                    self.assertEqual(c.execute(select(listings)).all(), [])

    def test_download_interruption_does_not_import(self) -> None:
        def interrupted():
            yield self.base
            raise ConnectionError("interrupted")
        self.apify.dataset.return_value.iterate_items.return_value = interrupted()
        with self.assertRaises(ConnectionError): self.execute()
        with self.engine.connect() as c:
            self.assertEqual(c.execute(select(listings)).all(), [])

    def test_unknown_or_stopped_checks_report_partial(self) -> None:
        self.seed("2")
        self.seed("3")
        with patch("app.pipeline.check_saved_listings", return_value={"active": 0, "deleted": 0, "unknown": 1}):
            report = self.execute()
        self.assertEqual(report["status"], "partial")

    def test_completeness_rejects_truncation_and_changed_logs(self) -> None:
        items = [BezrealitkyListing.model_validate({**self.base, "id": str(i)}) for i in range(16)]
        self.assertIsNone(completeness_problem(completion_log(16), items))
        for log, rows in [
            (completion_log(15), items[:15]), (completion_log(16), items[:10]),
            (completion_log(16).replace("page 2", "page 3"), items),
            (completion_log(16) + "\nERROR failed request", items),
            ("", items), (completion_log(0), []),
        ]:
            self.assertIsNotNone(completeness_problem(log, rows))

    def test_repeated_ids_are_deduplicated_using_newest_snapshot(self) -> None:
        self.apify.dataset.return_value.iterate_items.return_value = [
            {**self.base, "price": 22000, "scrapedAt": (self.started + timedelta(minutes=2)).isoformat()},
            {**self.base, "price": 20000},
        ]
        self.apify.run.return_value.log.return_value.get.return_value = completion_log(2)
        report = self.execute()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["duplicate_rows"], 1)
        self.assertEqual(report["imported"], 1)
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(listings.c.price)), 22000)

    def test_lock_prevents_overlap_and_releases_after_failure(self) -> None:
        with pipeline_lock(self.directory):
            with self.assertRaises(RuntimeError), pipeline_lock(self.directory):
                pass
        with self.assertRaises(ValueError), pipeline_lock(self.directory):
            raise ValueError("test")
        with pipeline_lock(self.directory):
            pass

    def test_explicit_empty_checker_scope_never_checks_everything(self) -> None:
        self.seed("1")
        with patch("app.check_availability.check_listing") as check:
            counts = check_saved_listings(self.engine, self.http, external_ids=[], limit=None)
        check.assert_not_called()
        self.assertEqual(sum(counts.values()), 0)


if __name__ == "__main__":
    unittest.main()
