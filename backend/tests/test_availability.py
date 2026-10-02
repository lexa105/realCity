import contextlib
import io
import json
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.availability import record_availability
from app.check_availability import AvailabilityResult, check_listing, check_saved_listings, classify_response, main
from app.database import listing_availability, listings, metadata
from app.ingest import import_items
from app.main import create_app


def page(props: object, status: int = 200, identifier: str = "1") -> httpx.Response:
    return httpx.Response(
        status,
        text=f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": props}})}</script>',
        request=httpx.Request("GET", f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{identifier}"),
    )


class AvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.api = self.enterContext(TestClient(create_app(self.engine)))
        self.item = dict(
            id="1", url="https://www.bezrealitky.cz/nemovitosti-byty-domy/1", title="Byt",
            transactionType="rent", propertyType="flat", city="Praha", currency="CZK",
            price=18000, scrapedAt="2026-09-24T10:00:00Z",
        )
        import_items(self.engine, [self.item, {**self.item, "id": "2"}])

    def observe(self, deleted: bool | None, hour: int = 11, identifier: str = "1") -> None:
        with self.engine.begin() as connection:
            record_availability(
                connection, "bezrealitky", identifier,
                datetime(2026, 9, 24, hour, tzinfo=timezone.utc),
                is_deleted=deleted, reason="listing_not_found" if deleted else None,
            )

    def test_deleted_hidden_before_pagination_and_included_on_request(self) -> None:
        self.observe(True)
        rows = self.api.get("/listings?limit=1").json()
        self.assertEqual([row["external_id"] for row in rows], ["2"])
        self.assertEqual(self.api.get("/listings?limit=1&offset=1").json(), [])
        rows = self.api.get("/listings?include_deleted=true&max_price=20000").json()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["is_deleted"])
        self.assertEqual(rows[0]["deletion_reason"], "listing_not_found")
        self.assertIsNotNone(rows[0]["deleted_at"])
        self.assertFalse(rows[1]["is_deleted"])

    def test_deletion_preserves_payload_and_direct_lookup(self) -> None:
        with self.engine.connect() as connection:
            before = connection.execute(select(listings)).all()
        self.observe(True)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(listings)).all(), before)
        response = self.api.get("/listings/bezrealitky/1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_deleted"])
        self.assertEqual(response.json()["price"], 18000)

    def test_unknown_and_repeated_checks_preserve_original_deletion_time(self) -> None:
        self.observe(True)
        original = self.api.get("/listings/bezrealitky/1").json()["deleted_at"]
        self.observe(None, 12)
        self.observe(True, 13)
        row = self.api.get("/listings/bezrealitky/1").json()
        self.assertEqual(row["deleted_at"], original)
        self.assertEqual(row["availability_checked_at"], "2026-09-24T13:00:00Z")

    def test_saved_dataset_does_not_reactivate_but_new_snapshot_does(self) -> None:
        self.observe(True)
        import_items(self.engine, [self.item])
        self.assertTrue(self.api.get("/listings/bezrealitky/1").json()["is_deleted"])
        import_items(self.engine, [{**self.item, "scrapedAt": "2026-09-24T12:00:00Z"}])
        row = self.api.get("/listings/bezrealitky/1").json()
        self.assertFalse(row["is_deleted"])
        self.assertIsNone(row["deleted_at"])
        self.assertIsNone(row["deletion_reason"])

    def test_older_and_equal_checks_cannot_overwrite_newer_evidence(self) -> None:
        self.observe(True, 12)
        self.observe(False, 11)
        self.observe(False, 12)
        self.assertTrue(self.api.get("/listings/bezrealitky/1").json()["is_deleted"])
        self.observe(False, 13)
        self.assertFalse(self.api.get("/listings/bezrealitky/1").json()["is_deleted"])
        self.observe(True, 9, identifier="2")
        self.assertFalse(self.api.get("/listings/bezrealitky/2").json()["is_deleted"])

    def test_partial_import_does_not_delete_absent_listings(self) -> None:
        import_items(self.engine, [{**self.item, "scrapedAt": "2026-09-24T12:00:00Z"}])
        self.assertEqual(len(self.api.get("/listings").json()), 2)

    def test_bounded_checker_and_dry_run(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text=page({"isDetail404": True}, 404).text)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, contextlib.redirect_stdout(io.StringIO()):
            counts = check_saved_listings(self.engine, client, limit=1, dry_run=True, delay_seconds=0)
            self.assertEqual(counts, {"active": 0, "deleted": 1, "unknown": 0})
            self.assertEqual(len(self.api.get("/listings").json()), 2)
            check_saved_listings(self.engine, client, limit=1, delay_seconds=0)
            self.assertEqual([row["external_id"] for row in self.api.get("/listings").json()], ["2"])
            check_saved_listings(self.engine, client, limit=1, delay_seconds=0)
            self.assertEqual(self.api.get("/listings").json(), [])

    def test_blocked_check_stops_batch_and_keeps_status(self) -> None:
        self.observe(True)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429))) as client:
            with contextlib.redirect_stdout(io.StringIO()):
                counts = check_saved_listings(self.engine, client, delay_seconds=0, external_ids=["1"])
        self.assertEqual(counts["unknown"], 1)
        self.assertTrue(self.api.get("/listings/bezrealitky/1").json()["is_deleted"])

    def test_all_checks_more_than_batch_cap_including_deleted(self) -> None:
        import_items(self.engine, [{**self.item, "id": str(identifier)} for identifier in range(3, 1002)])
        self.observe(True)
        with httpx.Client() as client, contextlib.redirect_stdout(io.StringIO()):
            with patch("app.check_availability.check_listing", return_value=AvailabilityResult(False, "provider_active")) as check:
                counts = check_saved_listings(self.engine, client, limit=None, dry_run=True, delay_seconds=0)
        self.assertEqual(counts, {"active": 1001, "deleted": 0, "unknown": 0})
        self.assertEqual({call.args[1] for call in check.call_args_list}, {str(i) for i in range(1, 1002)})
        self.assertTrue(self.api.get("/listings/bezrealitky/1").json()["is_deleted"])

    def test_all_cli_removes_limit_and_rejects_conflicting_options(self) -> None:
        with patch("sys.argv", ["check_availability", "--all", "--dry-run"]), \
                patch("app.check_availability.get_engine", return_value=self.engine), \
                patch("app.check_availability.check_saved_listings", return_value={}) as check, \
                contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertIsNone(check.call_args.kwargs["limit"])
        self.assertTrue(check.call_args.kwargs["dry_run"])
        with patch("sys.argv", ["check_availability", "--all", "--limit", "50"]), \
                patch("app.check_availability.get_engine") as get_engine, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 2)
        get_engine.assert_not_called()

    def test_existing_database_gets_additive_table_without_losing_listings(self) -> None:
        with self.engine.begin() as connection:
            listing_availability.drop(connection)
        metadata.create_all(self.engine)
        self.assertEqual(len(self.api.get("/listings").json()), 2)


class ProviderPageTests(unittest.TestCase):
    def test_active_inactive_and_not_found(self) -> None:
        for active in [True, False]:
            with self.subTest(active=active):
                result = classify_response(page({"apolloCache": {"Advert:1": {"id": "1", "active": active}}}), "1")
                self.assertEqual(result.is_deleted, not active)
        self.assertTrue(classify_response(page({"isDetail404": True}, 404), "1").is_deleted)
        self.assertTrue(classify_response(page({}, 410), "1").is_deleted)

    def test_errors_and_changed_markup_are_unknown(self) -> None:
        for response in [
            page({}, 404), page({}, 403), page({}, 429), page({}, 500), page({}),
            page(None), page({"apolloCache": []}),
            page({"apolloCache": {"Advert:2": {"id": "2", "active": False}}}),
            page({"apolloCache": {"Advert:1": {"id": "1", "active": "false"}}}),
            httpx.Response(200, text="captcha", request=httpx.Request("GET", "https://www.bezrealitky.cz/nemovitosti-byty-domy/1")),
        ]:
            with self.subTest(response=response):
                self.assertIsNone(classify_response(response, "1").is_deleted)

    def test_follows_same_listing_slug_but_never_external_redirect(self) -> None:
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path.endswith("/1"):
                return httpx.Response(301, headers={"Location": "/nemovitosti-byty-domy/1-nabidka"})
            return httpx.Response(200, text=page({"apolloCache": {"Advert:1": {"id": "1", "active": True}}}).text)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertFalse(check_listing(client, "1").is_deleted)
        self.assertEqual(len(requests), 2)
        for target in ["https://example.com/1", "/vyhledat", "/nemovitosti-byty-domy/2", "http://127.0.0.1/"]:
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location": target}))) as client:
                self.assertEqual(check_listing(client, "1").reason, "unexpected_redirect")

    def test_timeout_is_unknown(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertIsNone(check_listing(client, "1").is_deleted)
            self.assertEqual(check_listing(client, "../other").reason, "invalid_provider_id")


if __name__ == "__main__":
    unittest.main()
