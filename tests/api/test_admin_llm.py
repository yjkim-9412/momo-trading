import pytest

from analysis.llm.llm_factory import llm_factory


@pytest.mark.asyncio
async def test_admin_llm_status_endpoint(client, monkeypatch):
    async def fake_status():
        return {
            "current_provider": "CODEX_CLI",
            "selected_provider": "CODEX_CLI",
            "provider": "CODEX_CLI",
            "provider_name": "Codex CLI (로컬)",
            "tier1": {"provider": "CODEX_CLI", "model": "gpt-5.4", "reasoning_effort": "medium"},
            "tier2": {"provider": "CODEX_CLI", "model": "gpt-5.4", "reasoning_effort": "high"},
            "available_providers": [],
            "session_id": None,
        }

    monkeypatch.setattr(llm_factory, "get_llm_status", fake_status)

    response = await client.get("/api/v1/admin/llm/status")

    assert response.status_code == 200
    assert response.json()["data"]["selected_provider"] == "CODEX_CLI"


@pytest.mark.asyncio
async def test_admin_llm_usage_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        llm_factory,
        "get_llm_usage",
        lambda: {
            "provider": "CODEX_CLI",
            "provider_name": "Codex CLI (로컬)",
            "summary": {
                "total_sessions": 3,
                "total_messages": None,
                "input_tokens": None,
                "output_tokens": None,
                "cached_input_tokens": None,
                "first_session_date": None,
            },
            "model_usage": {},
            "daily_model_tokens": [],
            "provider_data": {},
            "app_usage": {"total_calls": 2},
        },
    )

    response = await client.get("/api/v1/admin/llm/usage")

    assert response.status_code == 200
    assert response.json()["data"]["provider"] == "CODEX_CLI"
    assert response.json()["data"]["summary"]["total_sessions"] == 3
