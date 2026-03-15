"""AI Trading Agent — Mixin 기반 패키지

장중: WebSocket 실시간 시세 → 이벤트 감지 → 즉시 분석/매매
장외: 오늘 성과 리뷰 + 피드백 학습
"""
from agent.trading_agent._types import MarketState  # noqa: F401
from agent.trading_agent._state_mixin import StateMixin
from agent.trading_agent._portfolio_mixin import PortfolioMixin
from agent.trading_agent._analysis_mixin import AnalysisMixin
from agent.trading_agent._cycle_mixin import CycleMixin
from agent.trading_agent._event_mixin import EventMixin

# 기존 테스트 patch 경로 호환을 위한 re-export (싱글톤 메서드 패치용)
from agent.decision_maker import decision_maker  # noqa: F401
from analysis.chart_analyzer import chart_analyzer  # noqa: F401
from analysis.feedback.context_builder import FeedbackContextBuilder  # noqa: F401
from analysis.llm.llm_factory import llm_factory  # noqa: F401
from core.database import AsyncSessionLocal  # noqa: F401
from core.events import event_bus  # noqa: F401
from scheduler.market_calendar import market_calendar  # noqa: F401
from services.activity_logger import activity_logger  # noqa: F401
from strategy.risk_manager import risk_manager  # noqa: F401
from realtime.event_detector import event_detector  # noqa: F401
from trading.mcp_client import mcp_client  # noqa: F401


class TradingAgent(
    StateMixin,      # __init__ 소유 — MRO 최우선
    PortfolioMixin,
    AnalysisMixin,
    CycleMixin,
    EventMixin,
):
    """
    AI 트레이딩 에이전트 — 장 시간에 맞춰 자동 운영

    장중: WebSocket 실시간 시세 → 이벤트 감지 → 즉시 분석/매매
    장외: 오늘 성과 리뷰 + 피드백 학습
    """
    pass


trading_agent = TradingAgent()
