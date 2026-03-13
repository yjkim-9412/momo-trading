from datetime import date, datetime
from urllib.parse import quote

import pytest
from sqlalchemy import delete

from models.agent_activity import AgentActivityLog
from scheduler.market_calendar import market_calendar
from tests.conftest import TestAsyncSessionLocal


async def _seed_activities(rows: list[AgentActivityLog]) -> None:
    async with TestAsyncSessionLocal() as session:
        await session.execute(delete(AgentActivityLog))
        session.add_all(rows)
        await session.commit()


@pytest.mark.asyncio
async def test_admin_activity_feed_uses_market_trading_date_for_us(client, monkeypatch):
    monkeypatch.setattr(
        market_calendar,
        "market_date",
        lambda market=None, dt=None: date(2026, 3, 13),
    )
    await _seed_activities(
        [
            AgentActivityLog(
                id="us-feed-1",
                market_scope="US",
                trading_date=date(2026, 3, 13),
                activity_type="LLM_CALL",
                phase="COMPLETE",
                summary="최근 미국장 대화",
                detail="{}",
                created_at=datetime(2026, 3, 14, 0, 5, 0),
            ),
            AgentActivityLog(
                id="us-feed-2",
                market_scope="US",
                trading_date=date(2026, 3, 12),
                activity_type="LLM_CALL",
                phase="COMPLETE",
                summary="이전 거래일 대화",
                detail="{}",
                created_at=datetime(2026, 3, 13, 0, 5, 0),
            ),
        ]
    )

    response = await client.get("/api/v1/admin/activities/feed?market_scope=US&limit=10")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["resolved_trading_date"] == "2026-03-13"
    assert payload["has_more"] is False
    assert [item["id"] for item in payload["items"]] == ["us-feed-1"]


@pytest.mark.asyncio
async def test_admin_activity_feed_supports_cursor_pagination(client, monkeypatch):
    monkeypatch.setattr(
        market_calendar,
        "market_date",
        lambda market=None, dt=None: date(2026, 3, 13),
    )
    await _seed_activities(
        [
            AgentActivityLog(
                id="feed-3",
                market_scope="US",
                trading_date=date(2026, 3, 13),
                activity_type="LLM_CALL",
                phase="COMPLETE",
                summary="세 번째",
                detail="{}",
                created_at=datetime(2026, 3, 14, 0, 3, 0),
            ),
            AgentActivityLog(
                id="feed-2",
                market_scope="US",
                trading_date=date(2026, 3, 13),
                activity_type="LLM_CALL",
                phase="COMPLETE",
                summary="두 번째",
                detail="{}",
                created_at=datetime(2026, 3, 14, 0, 2, 0),
            ),
            AgentActivityLog(
                id="feed-1",
                market_scope="US",
                trading_date=date(2026, 3, 13),
                activity_type="LLM_CALL",
                phase="COMPLETE",
                summary="첫 번째",
                detail="{}",
                created_at=datetime(2026, 3, 14, 0, 1, 0),
            ),
        ]
    )

    first = await client.get("/api/v1/admin/activities/feed?market_scope=US&limit=2")

    assert first.status_code == 200
    first_payload = first.json()["data"]
    assert [item["id"] for item in first_payload["items"]] == ["feed-3", "feed-2"]
    assert first_payload["has_more"] is True
    cursor = first_payload["next_cursor"]
    assert cursor["before_id"] == "feed-2"
    encoded_created_at = quote(cursor["before_created_at"], safe="")

    second = await client.get(
        "/api/v1/admin/activities/feed"
        f"?market_scope=US&limit=2&before_created_at={encoded_created_at}"
        f"&before_id={cursor['before_id']}"
    )

    assert second.status_code == 200
    second_payload = second.json()["data"]
    assert [item["id"] for item in second_payload["items"]] == ["feed-1"]
    assert second_payload["has_more"] is False
    assert second_payload["next_cursor"] is None


@pytest.mark.asyncio
async def test_admin_activities_default_to_market_trading_date_when_scope_present(client, monkeypatch):
    monkeypatch.setattr(
        market_calendar,
        "market_date",
        lambda market=None, dt=None: date(2026, 3, 13),
    )
    await _seed_activities(
        [
            AgentActivityLog(
                id="default-feed-1",
                market_scope="US",
                trading_date=date(2026, 3, 13),
                activity_type="EVENT",
                phase="COMPLETE",
                summary="현재 거래일 이벤트",
                detail="{}",
                created_at=datetime(2026, 3, 14, 0, 10, 0),
            ),
            AgentActivityLog(
                id="default-feed-2",
                market_scope="US",
                trading_date=date(2026, 3, 12),
                activity_type="EVENT",
                phase="COMPLETE",
                summary="이전 거래일 이벤트",
                detail="{}",
                created_at=datetime(2026, 3, 13, 0, 10, 0),
            ),
        ]
    )

    response = await client.get("/api/v1/admin/activities?market_scope=US&limit=10")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert [item["id"] for item in payload] == ["default-feed-1"]
