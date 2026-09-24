from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .database import get_engine, listings, metadata
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
        currency: str = Query(default="CZK", pattern=r"^[A-Z]{3}$"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Listing]:
        statement = select(listings.c.data).where(listings.c.currency == currency)
        if city is not None:
            statement = statement.where(listings.c.city == city)
        if transaction_type is not None:
            statement = statement.where(listings.c.transaction_type == transaction_type)
        if max_price is not None:
            statement = statement.where(listings.c.price <= max_price)
        statement = statement.order_by(listings.c.source, listings.c.external_id).limit(limit).offset(offset)
        with application.state.engine.connect() as connection:
            return [Listing.model_validate(data) for data in connection.execute(statement).scalars()]

    @application.get("/listings/{source}/{external_id}", response_model=Listing)
    def get_listing(source: str, external_id: str) -> Listing:
        statement = select(listings.c.data).where(
            listings.c.source == source, listings.c.external_id == external_id,
        )
        with application.state.engine.connect() as connection:
            data = connection.execute(statement).scalar_one_or_none()
        if data is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        return Listing.model_validate(data)

    return application


app = create_app()
