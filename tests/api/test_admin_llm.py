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
            "tier1": {
                "provider": "CODEX_CLI",
                "model": "gpt-5.4",
                "reasoning_effort": "medium",
                "display_name": "후보 분석 에이전트",
                "short_label": "후보 분석",
                "description": "차트·시장 컨텍스트를 바탕으로 매수 후보와 목표/손절을 1차 판단",
            },
            "tier1_profiles": {
                "scan": {
                    "provider": "CODEX_CLI",
                    "model": "gpt-5.4",
                    "reasoning_effort": "low",
                    "display_name": "시장 스캔 프로필",
                    "short_label": "스캔",
                    "description": "시장 스캔·스크리닝·뉴스 요약에 사용하는 저비용 추론 프로필",
                },
                "analysis": {
                    "provider": "CODEX_CLI",
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                    "display_name": "종목 판단 프로필",
                    "short_label": "판단",
                    "description": "종목 1차 분석·AI 한도 결정에 사용하는 기본 추론 프로필",
                },
            },
            "tier2": {
                "provider": "CODEX_CLI",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "display_name": "최종 검토 에이전트",
                "short_label": "최종 검토",
                "description": "1차 분석 결과를 리스크·포트폴리오 관점에서 재검증해 주문 승인 여부를 결정",
            },
            "available_providers": [],
            "session_id": None,
        }

    monkeypatch.setattr(llm_factory, "get_llm_status", fake_status)

    response = await client.get("/api/v1/admin/llm/status")

    assert response.status_code == 200
    assert response.json()["data"]["selected_provider"] == "CODEX_CLI"
    assert response.json()["data"]["tier1"]["display_name"] == "후보 분석 에이전트"
    assert response.json()["data"]["tier1_profiles"]["scan"]["reasoning_effort"] == "low"
    assert response.json()["data"]["tier2"]["short_label"] == "최종 검토"


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
