import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import JSON, Column, Float, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
metadata = MetaData()
listings = Table(
    "listings", metadata,
    Column("source", String(50), primary_key=True),
    Column("external_id", String(255), primary_key=True),
    Column("city", String(255), index=True),
    Column("transaction_type", String(20), nullable=False, index=True),
    Column("price", Float, index=True),
    Column("currency", String(3), nullable=False),
    Column("scraped_at", String(40), nullable=False),
    Column("data", JSON, nullable=False),
    Column("raw_data", JSON, nullable=False),
)


def get_engine(database_url: str | None = None) -> Engine:
    load_dotenv(BACKEND_DIR / ".env")
    engine = create_engine(
        database_url or os.getenv("DATABASE_URL")
        or f"sqlite:///{BACKEND_DIR / 'realcity.db'}",
        pool_pre_ping=True,
    )
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        engine.dispose()
        raise ValueError("Supported databases are SQLite and PostgreSQL")
    return engine
