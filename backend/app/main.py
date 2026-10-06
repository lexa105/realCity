from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Select, select
from sqlalchemy.engine import Engine, Row

from .database import get_engine, listing_availability, listings, metadata
from .schemas import Listing


def create_app(database_engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = database_engine if database_engine is not None else get_engine()
        application.state.engine = engine
        metadata.create_all(engine)
        try:
            yield
        finally:
            if database_engine is None:
                engine.dispose()

    application = FastAPI(title="realCity API", lifespan=lifespan)

    def listing_query() -> Select[Any]:
        return select(
            listings.c.data, listing_availability.c.deleted_at,
            listing_availability.c.checked_at, listing_availability.c.deletion_reason,
        ).select_from(listings.outerjoin(listing_availability))

    def listing_response(row: Row[Any]) -> Listing:
        return Listing.model_validate({
            **row.data,
            "is_deleted": row.deleted_at is not None,
            "deleted_at": row.deleted_at,
            "availability_checked_at": row.checked_at,
            "deletion_reason": row.deletion_reason,
        })

    web_dir = Path(__file__).resolve().parents[2] / "web"
    application.mount("/static", StaticFiles(directory=web_dir), name="static")

    @application.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        with application.state.engine.connect() as connection:
            connection.execute(select(1))
        return {"status": "ok"}

    @application.get("/listings", response_model=list[Listing])
    def list_listings(
        city: str | None = None,
        transaction_type: Literal["rent", "sale"] | None = None,
        max_price: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        min_price: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        min_area: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        max_area: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        disposition: list[str] | None = Query(default=None),
        include_deleted: bool = False,
        currency: str = Query(default="CZK", pattern=r"^[A-Z]{3}$"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Listing]:
        if min_price is not None and max_price is not None and min_price > max_price:
            raise HTTPException(status_code=422, detail="Minimum price exceeds maximum price")
        if min_area is not None and max_area is not None and min_area > max_area:
            raise HTTPException(status_code=422, detail="Minimum area exceeds maximum area")
        statement = listing_query().where(listings.c.currency == currency)
        if not include_deleted:
            statement = statement.where(listing_availability.c.deleted_at.is_(None))
        if min_price is not None:
            statement = statement.where(listings.c.price >= min_price)
        if min_area is not None:
            statement = statement.where(listings.c.data["area"].as_float() >= min_area)
        if max_area is not None:
            statement = statement.where(listings.c.data["area"].as_float() <= max_area)
        if disposition:
            statement = statement.where(listings.c.data["disposition"].as_string().in_(disposition))
        if city is not None:
            statement = statement.where(listings.c.city == city)
        if transaction_type is not None:
            statement = statement.where(listings.c.transaction_type == transaction_type)
        if max_price is not None:
            statement = statement.where(listings.c.price <= max_price)
        statement = statement.order_by(listings.c.source, listings.c.external_id).limit(limit).offset(offset)
        with application.state.engine.connect() as connection:
            return [listing_response(row) for row in connection.execute(statement)]

    @application.get("/listings/{source}/{external_id}", response_model=Listing)
    def get_listing(source: str, external_id: str) -> Listing:
        statement = listing_query().where(
            listings.c.source == source, listings.c.external_id == external_id,
        )
        with application.state.engine.connect() as connection:
            data = connection.execute(statement).one_or_none()
        if data is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        return listing_response(data)

    @application.get("/listing/{source}/{external_id}", include_in_schema=False)
    def listing_page(source: str, external_id: str) -> FileResponse:
        # Use the same lookup as the API so nonexistent links return a real 404.
        get_listing(source, external_id)
        return FileResponse(web_dir / "detail.html")

    return application


app = create_app()
