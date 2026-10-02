"""Check saved Bezrealitky listings without running a paid Apify search."""

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .availability import record_availability
from .database import get_engine, listing_availability, listings, metadata


@dataclass(frozen=True)
class AvailabilityResult:
    is_deleted: bool | None
    reason: str


def is_listing_url(url: str, external_id: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"www.bezrealitky.cz", "bezrealitky.cz"}
        and re.fullmatch(
            rf"/nemovitosti-byty-domy/{re.escape(external_id)}(?:-[^/]*)?/?", parsed.path,
        ) is not None
    )


def classify_response(response: httpx.Response, external_id: str) -> AvailabilityResult:
    """Require provider evidence; error pages and unrelated adverts are inconclusive."""
    if not is_listing_url(str(response.url), external_id):
        return AvailabilityResult(None, "unexpected_url")
    if response.status_code == 410:
        return AvailabilityResult(True, "http_410")
    if response.status_code not in {200, 404}:
        return AvailabilityResult(None, f"http_{response.status_code}")
    script = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        props = json.loads(script.get_text() if script else "")["props"]["pageProps"]
        if not isinstance(props, dict):
            return AvailabilityResult(None, "unrecognized_page")
        if props.get("isDetail404") is True:
            return AvailabilityResult(True, "listing_not_found")
        if response.status_code == 200:
            advert = props.get("apolloCache", {}).get(f"Advert:{external_id}", {})
            if str(advert.get("id")) == external_id:
                if advert.get("active") is False:
                    return AvailabilityResult(True, "provider_inactive")
                if advert.get("active") is True:
                    return AvailabilityResult(False, "provider_active")
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return AvailabilityResult(None, "unrecognized_page")


def check_listing(client: httpx.Client, external_id: str) -> AvailabilityResult:
    if not re.fullmatch(r"[0-9]+", external_id):
        return AvailabilityResult(None, "invalid_provider_id")
    # Construct a provider URL rather than fetching arbitrary URLs from imports.
    url = f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{external_id}"
    try:
        for _ in range(4):
            response = client.get(url, follow_redirects=False)
            if response.is_redirect:
                target = urljoin(url, response.headers.get("location", ""))
                if not is_listing_url(target, external_id):
                    return AvailabilityResult(None, "unexpected_redirect")
                url = target
                continue
            return classify_response(response, external_id)
    except httpx.HTTPError:
        return AvailabilityResult(None, "network_error")
    return AvailabilityResult(None, "too_many_redirects")


def check_saved_listings(
    engine: Engine,
    client: httpx.Client,
    *,
    limit: int | None = 50,
    external_ids: list[str] | None = None,
    dry_run: bool = False,
    delay_seconds: float = 1.0,
) -> dict[str, int]:
    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    statement = select(listings.c.external_id).select_from(
        listings.outerjoin(listing_availability)
    ).where(listings.c.source == "bezrealitky")
    if external_ids is not None:
        statement = statement.where(listings.c.external_id.in_(external_ids))
    statement = statement.order_by(
        listing_availability.c.checked_at.asc().nulls_first(), listings.c.external_id,
    )
    if limit is not None:
        statement = statement.limit(limit)
    with engine.connect() as connection:
        identifiers = list(connection.execute(statement).scalars())
    counts = {"active": 0, "deleted": 0, "unknown": 0}
    for index, external_id in enumerate(identifiers):
        if index:
            time.sleep(delay_seconds)
        # Timestamp the beginning of the request, so a newer concurrent import wins.
        checked_at = datetime.now(timezone.utc)
        result = check_listing(client, external_id)
        status = "unknown" if result.is_deleted is None else "deleted" if result.is_deleted else "active"
        counts[status] += 1
        if not dry_run:
            with engine.begin() as connection:
                record_availability(
                    connection, "bezrealitky", external_id, checked_at,
                    is_deleted=result.is_deleted, reason=result.reason,
                )
        print(f"{external_id}: {status} ({result.reason})", flush=True)
        # Avoid repeatedly hitting a provider that is blocking or failing requests.
        if result.reason in {"http_403", "http_429", "network_error"} or result.reason.startswith("http_5"):
            break
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Check availability of saved Bezrealitky listings")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int, default=50, help="Maximum checks (1–1000, default: 50)")
    scope.add_argument("--all", action="store_true", help="Check all matching saved listings without a count limit")
    parser.add_argument("--external-id", action="append", help="Check a specific saved ID; repeat for multiple IDs")
    parser.add_argument("--dry-run", action="store_true", help="Report results without changing listing status")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    engine = get_engine()
    try:
        metadata.create_all(engine)
        with httpx.Client(timeout=20, headers={"User-Agent": "realCity-availability/1.0"}) as client:
            counts = check_saved_listings(
                engine, client, limit=None if args.all else args.limit,
                external_ids=args.external_id, dry_run=args.dry_run,
            )
        print(f"{'Dry run: ' if args.dry_run else ''}{json.dumps(counts)}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
