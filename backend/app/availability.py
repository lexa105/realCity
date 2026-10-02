"""Store availability independently of the last listing payload."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from .database import listing_availability, listings


def record_availability(
    connection: Connection,
    source: str,
    external_id: str,
    checked_at: datetime,
    *,
    is_deleted: bool | None,
    reason: str | None = None,
) -> None:
    """Unknown checks advance the check queue but never change availability.

    Older observations cannot overwrite a newer check or listing snapshot.
    deleted_at is when removal was first observed, not the provider's removal time.
    """
    timestamp = checked_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    identity = (listings.c.source == source) & (listings.c.external_id == external_id)
    # Serialize observations and imports for this listing on PostgreSQL. SQLite
    # serializes writers; the conflict condition below also guards stale checks.
    scraped_at = connection.execute(
        select(listings.c.scraped_at).where(identity).with_for_update()
    ).scalar_one()
    if timestamp < scraped_at:
        return
    insert = sqlite_insert if connection.dialect.name == "sqlite" else postgres_insert
    statement = insert(listing_availability).values(
        source=source, external_id=external_id, checked_at=timestamp,
        deleted_at=timestamp if is_deleted else None,
        deletion_reason=reason if is_deleted else None,
    )
    changes = {"checked_at": statement.excluded.checked_at}
    if is_deleted is not None:
        changes["deleted_at"] = (
            func.coalesce(listing_availability.c.deleted_at, statement.excluded.deleted_at)
            if is_deleted else None
        )
        changes["deletion_reason"] = reason if is_deleted else None
    connection.execute(statement.on_conflict_do_update(
        index_elements=[listing_availability.c.source, listing_availability.c.external_id],
        set_=changes,
        where=statement.excluded.checked_at > listing_availability.c.checked_at,
    ))
