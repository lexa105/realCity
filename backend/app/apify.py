"""Fetch Bezrealitky listings from Apify and import them into our database."""

import argparse
import os
from datetime import timedelta
from decimal import Decimal
from typing import Any

from apify_client import ApifyClient
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from .database import BACKEND_DIR, get_engine, metadata
from .ingest import import_items

BEZREALITKY_ACTOR_ID = "QsjkAHuaFwcSxukzl"


class ScrapeOptions(BaseModel):
    location: str = Field(default="Praha", min_length=1)
    limit: int = Field(default=10, ge=1, le=1000)
    max_price: int | None = Field(default=None, ge=0)
    max_cost_usd: Decimal = Field(default=Decimal("0.01"), gt=0, allow_inf_nan=False)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)

    def actor_input(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "includeDetails": True,
            "language": "cs",
            "location": self.location,
            "maxResults": self.limit,
            "propertyType": "flat",
            "transactionType": "rent",
        }
        if self.max_price is not None:
            payload["priceTo"] = self.max_price
        return payload


def fetch_items(
    client: ApifyClient,
    options: ScrapeOptions,
    *,
    dataset_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run the actor unless an existing Bezrealitky dataset is supplied."""
    if dataset_id is None:
        run = client.actor(BEZREALITKY_ACTOR_ID).call(
            run_input=options.actor_input(),
            # max_items can produce a budget smaller than the actor startup fee.
            # Keep the monetary budget separate from the requested listing count.
            max_total_charge_usd=options.max_cost_usd,
            run_timeout=timedelta(seconds=options.timeout_seconds),
            logger=None,
        )
        if run is None:
            raise RuntimeError("Apify returned no run; no listings were imported.")
        if run.status != "SUCCEEDED":
            raise RuntimeError(f"Apify run {run.id} ended with status {run.status}; no listings were imported.")
        dataset_id = run.default_dataset_id
    if not dataset_id or not dataset_id.strip():
        raise ValueError("A nonempty Apify dataset ID is required.")
    # iterate_items handles Apify pagination. Preserve raw fields for validation/storage.
    return list(client.dataset(dataset_id).iterate_items(limit=options.limit))


def sync_listings(
    engine: Engine,
    client: ApifyClient,
    options: ScrapeOptions,
    *,
    dataset_id: str | None = None,
) -> int:
    items = fetch_items(client, options, dataset_id=dataset_id)
    metadata.create_all(engine)
    return import_items(engine, items)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Bezrealitky listings from Apify")
    parser.add_argument("--dataset-id", help="Read an existing dataset instead of starting a paid actor run")
    parser.add_argument("--location", default="Praha", help="Actor search location (default: Praha)")
    parser.add_argument("--limit", type=int, default=10, help="Maximum listings to fetch/import (1–1000)")
    parser.add_argument("--max-price", type=int, help="Actor priceTo filter in CZK")
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal("0.01"),
                        help="Apify run charge limit in USD, including startup (default: 0.01)")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Actor execution timeout (default: 300)")
    args = parser.parse_args()
    options = ScrapeOptions(
        location=args.location, limit=args.limit,
        max_price=args.max_price, timeout_seconds=args.timeout_seconds,
        max_cost_usd=args.max_cost_usd,
    )
    load_dotenv(BACKEND_DIR / ".env")
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        parser.error("Set APIFY_API_TOKEN in backend/.env or the environment.")

    client = ApifyClient(token)
    engine = get_engine()
    try:
        print("Reading Apify dataset..." if args.dataset_id else "Running Bezrealitky actor on Apify...")
        count = sync_listings(engine, client, options, dataset_id=args.dataset_id)
        print(f"Processed {count} listings; duplicates updated, older snapshots ignored.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
