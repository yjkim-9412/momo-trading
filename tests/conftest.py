import sys
import types

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import get_async_db, get_async_db_with_transaction
from models.base import Base

# ── Async Test DB (in-memory SQLite) ──
ASYNC_TEST_DB_URL = "sqlite+aiosqlite://"

test_async_engine = create_async_engine(
    ASYNC_TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestAsyncSessionLocal = async_sessionmaker(
    test_async_engine, expire_on_commit=False
)


class _PandasTAStub(types.ModuleType):
    """테스트 import용 pandas_ta shim"""

    def __getattr__(self, name: str):
        if name == "ichimoku":
            return lambda *args, **kwargs: (pd.DataFrame(), pd.DataFrame())
        if name in {"macd", "bbands", "stoch", "adx"}:
            return lambda *args, **kwargs: pd.DataFrame()
        return lambda *args, **kwargs: pd.Series(dtype=float)


if "pandas_ta" not in sys.modules:
    sys.modules["pandas_ta"] = _PandasTAStub("pandas_ta")


async def override_get_async_db():
    async with TestAsyncSessionLocal() as session:
        yield session


async def override_get_async_db_with_transaction():
    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            yield session


@pytest.fixture(scope="session", autouse=True)
async def create_tables():
    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
async def client():
    from main import app

    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_async_db_with_transaction] = (
        override_get_async_db_with_transaction
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
