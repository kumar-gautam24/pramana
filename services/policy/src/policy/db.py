"""Async engine and session factory.

`create_async_engine` opens no connection, so constructing it proves nothing about the
URL beyond it being parseable -- a database that does not exist surfaces only on the
first query. Startup probes the engine (see the lifespan in main.py) so misconfiguration
fails before the service accepts traffic."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from policy.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
