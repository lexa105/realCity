import argparse
import json
from datetime import timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from .database import get_engine, listings, metadata
from .schemas import BezrealitkyListing, normalize


def import_items(engine: Engine, items: list[dict[str, Any]]) -> int:
    validated = [BezrealitkyListing.model_validate(item) for item in items]
    insert = sqlite_insert if engine.dialect.name == "sqlite" else postgres_insert
    with engine.begin() as connection:
        for raw, item in zip(items, validated, strict=True):
            listing = normalize(item)
            statement = insert(listings).values(
                source=listing.source, external_id=listing.external_id,
                city=listing.city, transaction_type=listing.transaction_type,
                price=listing.price, currency=listing.currency,
                scraped_at=listing.scraped_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                data=listing.model_dump(mode="json"), raw_data=raw,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[listings.c.source, listings.c.external_id],
                set_={column.name: statement.excluded[column.name]
                      for column in listings.columns if not column.primary_key},
                where=statement.excluded.scraped_at >= listings.c.scraped_at,
            )
            connection.execute(statement)
    return len(validated)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import saved Bezrealitky JSON into the database")
    parser.add_argument("path", type=Path, help="A single listing object or an array of listings")
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else [payload]
    engine = get_engine()
    try:
        metadata.create_all(engine)
        count = import_items(engine, items)
        print(f"Processed {count} listings; duplicates updated, older snapshots ignored.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
