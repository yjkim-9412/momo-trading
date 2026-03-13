from collections.abc import Iterable

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

# ── Async Engine & Session ──
async_engine = create_async_engine(
    settings.async_database_url,
    echo=settings.is_local,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


async def get_existing_table_names() -> set[str]:
    """현재 DB에 존재하는 테이블 목록을 반환한다."""

    def _load(sync_connection) -> set[str]:
        return set(inspect(sync_connection).get_table_names())

    async with async_engine.begin() as connection:
        return await connection.run_sync(_load)


async def validate_database_schema(required_tables: Iterable[str] | None = None) -> None:
    """애플리케이션이 기대하는 테이블이 모두 존재하는지 검증한다."""
    if required_tables is None:
        from models import Base

        expected_tables = set(Base.metadata.tables.keys())
    else:
        expected_tables = set(required_tables)

    existing_tables = await get_existing_table_names()
    missing_tables = sorted(expected_tables - existing_tables)
    if not missing_tables:
        return

    preview = ", ".join(missing_tables[:5])
    suffix = "" if len(missing_tables) <= 5 else f" (+{len(missing_tables) - 5} more)"
    raise RuntimeError(
        "DB schema is not ready. "
        f"Missing tables: {preview}{suffix}. "
        "Run `python -m alembic upgrade head` before starting the server."
    )


# ── Async DI Generators ──
async def get_async_db():
    """읽기 전용 async 세션"""
    async with AsyncSessionLocal() as session:
        yield session


async def get_async_db_with_transaction():
    """쓰기용 async 세션 — 자동 commit/rollback"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session
