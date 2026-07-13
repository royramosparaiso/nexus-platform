"""Database engine + session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.config import get_settings

_settings = get_settings()

# Normalize the URL: SQLAlchemy uses `+asyncpg` or `+psycopg` for async drivers.
_url = _settings.database_url
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_async_engine(_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
