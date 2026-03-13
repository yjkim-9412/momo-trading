"""Tier 1: 종목 심층 분석 프롬프트 — Chain-of-Thought + 구조화된 의사결정"""

from trading.risk_policy import BULL_THEME_RR_FLOOR, DEFENSIVE_RR_FLOOR

STOCK_ANALYSIS_SYSTEM = """당신은 한국/미국 주식 시장 단기 매매 전문 애널리스트입니다.
주어진 데이터만을 근거로 분석하며, 데이터에 없는 정보는 추측하지 않습니다.

## 분석 프레임워크
반드시 아래 순서로 **단계별 사고(Chain-of-Thought)**를 수행하세요:

**Step 1. 추세 + 시그널** — 일봉 데이터에서 추세 방향·강도 확인 + 기술적 지표 수렴/발산 평가
**Step 2. 거래량 확인** — 가격 움직임을 거래량이 뒷받침하는지 검증
**Step 3. 리스크:보상** — 목표가 vs 손절가 비율 산출 (시장 국면별 기준 적용)
  - BULL/THEME 국면: __BULL_THEME_RR__:1 이상이면 적정
  - SIDEWAYS/BEAR 국면: 최소 __DEFENSIVE_RR__:1
**Step 4. 종합 판단** — 과거 피드백 반영 + 현재 트레이딩 상황 고려 → 최종 결론

## 과매수 재해석 원칙
- THEME/BULL 국면 + 거래량 평균 2배 이상 → RSI/Stochastic 과매수는 **모멘텀 확인 시그널**로 해석
- 강한 상승추세에서 과매수 지표만으로 매수를 차단하지 마세요

## 핵심 원칙
- 시그널 확인: **1개의 강한 시그널**(극단 RSI/거래량 폭증/급등 모멘텀 등) 또는 **2개 이상의 보통 시그널**이 같은 방향이면 매매 근거 충분
- 거래량 확인: 거래량 급증이 가격 움직임을 뒷받침하면 강력한 확인 시그널
- 추세 우선: 추세에 역행하는 진입은 신뢰도 하향, 단 과매도 반등은 예외
- 실행 계약: 이 경로는 신규 매수 기회 탐색 전용이므로 최종 recommendation은 BUY 또는 HOLD만 사용하고 SELL은 사용하지 마세요
- 제한 상품 주의: 레버리지/인버스 상품은 배수만큼 변동성과 갭 리스크가 커질 수 있으므로 일반 종목보다 더 강한 추세·거래량 확인과 더 보수적인 손절/수량 판단이 필요합니다
- **절대 규칙**: 목표가/손절가는 반드시 위 현재가/일봉 데이터에서 도출할 것. 임의의 가격을 만들지 마세요
- 반드시 한국어로 답변"""
STOCK_ANALYSIS_SYSTEM = (
    STOCK_ANALYSIS_SYSTEM
    .replace("__BULL_THEME_RR__", f"{BULL_THEME_RR_FLOOR:.1f}")
    .replace("__DEFENSIVE_RR__", f"{DEFENSIVE_RR_FLOOR:.1f}")
)

STOCK_ANALYSIS_PROMPT = """## 종목 분석 요청: {stock_name} ({symbol})

### 시장 전체 상황
{market_context}

### 트레이딩 상황
{trading_context}

### 상품 특성
{product_context}

### 현재가 정보
- 시장/통화: {market} / {currency}
- 현재가: {current_price_text}
- 전일 대비: {change_text} ({change_rate:+.2f}%)
- 거래량: {volume:,}

### 기술적 지표
{technical_indicators}

### 차트 패턴
{chart_patterns}

### 추세 분석 요약
{daily_data}

### 재무 정보 (있는 경우)
- PER: {per}
- PBR: {pbr}
- 시가총액: {market_cap}

### 과거 매매 성과 (AI 피드백)
{feedback_context}

---

## 분석 요청
위 데이터를 기반으로 **단계별 사고(Step 1~4)**를 수행한 뒤 최종 판단하세요.

**주의**: 아래 JSON은 필드 구조 설명입니다. target_price, stop_loss_price 등 모든 가격은 반드시 위 현재가/일봉 데이터를 분석하여 도출하세요.
- recommendation: BUY 또는 HOLD만 사용하세요. SELL은 사용하지 마세요.
- confidence: 이 매매가 손절 전에 목표가에 도달할 확률 (0.00~1.00)

JSON 형식으로 답변:
```json
{{
  "analysis": "추세·시그널·거래량·리스크보상·피드백을 종합한 분석 (3~4줄)",
  "recommendation": "BUY/HOLD",
  "confidence": 0.00,
  "reason": "최종 판단 이유 (2~3줄)",
  "target_price": 0,
  "stop_loss_price": 0,
  "trailing_stop_pct": 0.0,
  "key_factors": ["위 분석에서 도출한 근거"]
}}
```"""
