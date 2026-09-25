import unittest
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from apify_client import ApifyClient
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.apify import ScrapeOptions, fetch_items, sync_listings
from app.main import create_app


class ApifyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.client = MagicMock(spec=ApifyClient)
        self.client.actor.return_value.call.return_value = SimpleNamespace(
            id="run-1", status="SUCCEEDED", default_dataset_id="dataset-1",
        )
        self.item = {
            "id": "123", "url": "https://example.com/123", "title": "Praha flat",
            "transactionType": "rent", "propertyType": "flat", "city": "Praha",
            "price": 18000, "currency": "CZK", "scrapedAt": "2026-09-24T10:00:00Z",
        }
        self.client.dataset.return_value.iterate_items.return_value = [self.item]

    def test_actor_results_reach_api_and_reimports_update_one_row(self) -> None:
        options = ScrapeOptions(limit=5, max_price=20000)
        with TestClient(create_app(self.engine)) as api:
            self.assertEqual(sync_listings(self.engine, self.client, options), 1)
            kwargs = self.client.actor.return_value.call.call_args.kwargs
            self.assertEqual(kwargs["run_input"]["maxResults"], 5)
            self.assertNotIn("max_items", kwargs)
            self.assertEqual(kwargs["max_total_charge_usd"], Decimal("0.01"))
            self.assertEqual(kwargs["run_input"]["priceTo"], 20000)
            self.assertEqual(kwargs["run_timeout"], timedelta(seconds=300))
            self.client.dataset.assert_called_with("dataset-1")
            self.client.dataset.return_value.iterate_items.assert_called_with(limit=5)
            self.client.dataset.return_value.iterate_items.return_value = [
                {**self.item, "price": 19000, "scrapedAt": "2026-09-24T11:00:00Z"},
            ]
            sync_listings(self.engine, self.client, options, dataset_id="saved")
            self.client.dataset.return_value.iterate_items.return_value = [self.item]
            sync_listings(self.engine, self.client, options, dataset_id="saved")
            rows = api.get('/listings?city=Praha').json()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["price"], 19000)

    def test_existing_dataset_does_not_launch_actor(self) -> None:
        self.assertEqual(fetch_items(self.client, ScrapeOptions(), dataset_id="saved"), [self.item])
        self.client.actor.assert_not_called()

    def test_unsuccessful_runs_never_read_dataset(self) -> None:
        for status in ["FAILED", "TIMED-OUT", "ABORTED", "RUNNING"]:
            with self.subTest(status=status):
                self.client.actor.return_value.call.return_value.status = status
                with self.assertRaises(RuntimeError):
                    fetch_items(self.client, ScrapeOptions())
        self.client.actor.return_value.call.return_value = None
        with self.assertRaises(RuntimeError):
            fetch_items(self.client, ScrapeOptions())
        self.client.dataset.assert_not_called()

    def test_invalid_batch_does_not_save_valid_rows(self) -> None:
        self.client.dataset.return_value.iterate_items.return_value = [self.item, {**self.item, "price": -1}]
        with TestClient(create_app(self.engine)) as api:
            with self.assertRaises(ValidationError):
                sync_listings(self.engine, self.client, ScrapeOptions())
            self.assertEqual(api.get('/listings').json(), [])

    def test_download_failure_does_not_import_partial_results(self) -> None:
        def interrupted_download():
            yield self.item
            raise ConnectionError("Download interrupted")
        self.client.dataset.return_value.iterate_items.return_value = interrupted_download()
        with TestClient(create_app(self.engine)) as api:
            with self.assertRaises(ConnectionError):
                sync_listings(self.engine, self.client, ScrapeOptions())
            self.assertEqual(api.get('/listings').json(), [])

    def test_custom_budget_is_independent_of_listing_limit(self) -> None:
        fetch_items(self.client, ScrapeOptions(limit=3, max_cost_usd=Decimal("0.02")))
        kwargs = self.client.actor.return_value.call.call_args.kwargs
        self.assertEqual(kwargs["max_total_charge_usd"], Decimal("0.02"))
        self.assertEqual(kwargs["run_input"]["maxResults"], 3)
        self.assertNotIn("max_items", kwargs)

    def test_budget_must_be_positive_and_finite(self) -> None:
        for value in ["0", "-1", "NaN", "Infinity"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ScrapeOptions(max_cost_usd=Decimal(value))

    def test_options_reject_invalid_limits(self) -> None:
        for limit in [0, -1, 1001]:
            with self.assertRaises(ValidationError):
                ScrapeOptions(limit=limit)


if __name__ == "__main__":
    unittest.main()
