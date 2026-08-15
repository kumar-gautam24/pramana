"""Async engine and session factory.

Created at import so a bad DATABASE_URL fails the service at startup rather than on the
first query."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from policy.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
