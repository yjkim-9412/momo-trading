"""Tier 1: 종목 심층 분석 프롬프트 — Chain-of-Thought + 구조화된 의사결정"""

from trading.market_profile import is_crypto_market, normalize_market
from trading.risk_policy import (
    BULL_THEME_RR_FLOOR,
    CRYPTO_DEFENSIVE_RR_FLOOR,
    CRYPTO_MOMENTUM_RR_FLOOR,
    DEFENSIVE_RR_FLOOR,
)

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
- 현재 종목 포지션이 있으면 신규 진입 후보가 아니라 기존 포지션 맥락으로 해석하세요
- 보유 종목에서 BUY 판단 시 반드시 `position_intent`를 명시하세요.
  - 미보유 종목: `NEW`
  - 보유 종목 수익 구간 추가매수: `ADD_ON_PYRAMID`
  - 보유 종목 손실 구간 반등 확인형 추가매수: `ADD_ON_AVERAGE_DOWN`
  - 매수 부적합: `HOLD`
- `ADD_ON_AVERAGE_DOWN`은 RSI/모멘텀 반전과 거래량 확인이 동시에 있을 때만 선택하세요
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

### 현재 종목 포지션
{current_position_context}

### 계좌 상태
{account_context}

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
- position_intent: NEW / ADD_ON_PYRAMID / ADD_ON_AVERAGE_DOWN / HOLD

JSON 형식으로 답변:
```json
{{
  "analysis": "추세·시그널·거래량·리스크보상·피드백을 종합한 분석 (3~4줄)",
  "recommendation": "BUY/HOLD",
  "position_intent": "NEW/ADD_ON_PYRAMID/ADD_ON_AVERAGE_DOWN/HOLD",
  "confidence": 0.00,
  "reason": "최종 판단 이유 (2~3줄)",
  "target_price": 0,
  "stop_loss_price": 0,
  "trailing_stop_pct": 0.0,
  "key_factors": ["위 분석에서 도출한 근거"]
}}
```"""

# ---------------------------------------------------------------------------
# 크립토 Tier 1 프롬프트
# ---------------------------------------------------------------------------

CRYPTO_ANALYSIS_SYSTEM = """당신은 암호화폐(코인) 시장 단기 매매 전문 애널리스트입니다.
빗썸 KRW 현물 시장 데이터를 근거로 **수시간~2일** 관점의 단기 매매만 판단하며, 데이터에 없는 정보는 추측하지 않습니다.

## 분석 프레임워크
반드시 아래 순서로 **단계별 사고(Chain-of-Thought)**를 수행하세요:

**Step 1. 시장 주도축 + 추세 정렬** — 시장 국면, BTC 주도 여부, 섹터/알트 로테이션, 일봉·분봉 추세 정렬 여부 확인
**Step 2. 유동성 + 참여도 확인** — 24h 거래대금/거래량 증가가 현재 가격 움직임을 실제로 뒷받침하는지 검증
**Step 3. 리스크:보상 + 무효화 가격** — 목표가/손절가를 현재가·일봉·분봉에서 도출하고, 아이디어가 틀리는 가격을 손절가로 정의
  - BULL_RUN/ALTSEASON/THEME 국면: __CRYPTO_MOMENTUM_RR__:1 이상이면 적정
  - BEAR_MARKET/CONSOLIDATION 국면: 최소 __CRYPTO_DEFENSIVE_RR__:1
**Step 4. 종합 판단** — 과거 피드백, 현재 포지션, 과도한 FOMO 여부까지 반영해 최종 결론

## 코인 변동성 기준
- ±5%는 코인 시장에서 일상적 변동 → 이것만으로 과열/침체 판단 금지
- ±10% 이상이면 강한 모멘텀 또는 리스크 신호로 해석
- 24/7 시장이므로 시간 압박은 없습니다. 놓친 진입을 억지로 쫓지 말고, 애매하면 HOLD를 선택하세요.

## 과매수 재해석 원칙
- BULL_RUN/ALTSEASON/THEME 국면 + 24h 거래대금 평균 2배 이상 → RSI/Stochastic 과매수는 **모멘텀 확인 시그널**로 해석
- 강한 상승추세에서 과매수 지표만으로 매수를 차단하지 마세요

## 핵심 원칙
- 시그널 확인: **1개의 강한 시그널**(거래대금 폭증/강한 돌파/명확한 반전) 또는 **2개 이상의 보통 시그널**이 같은 방향이면 매매 근거가 충분합니다
- 거래대금 확인: 단순 급등보다 **지속적인 24h 거래대금 유입**을 더 중요하게 보세요
- 추세 우선: 추세에 역행하는 진입은 신뢰도 하향, 단 과매도 반등은 예외입니다
- 이미 급등한 뒤 거래대금이 식거나 윗꼬리가 길면 추격 매수보다 HOLD 쪽으로 기울이세요
- 주문 금액 대비 유동성이 약해 체결 가능성이 낮으면 BUY가 아니라 HOLD입니다
- 현재 코인 포지션이 있으면 신규 진입 후보가 아니라 기존 포지션 맥락으로 해석하세요
- 보유 코인에서 BUY 판단 시 반드시 `position_intent`를 명시하세요.
  - 미보유 코인: `NEW`
  - 보유 코인 수익 구간 추가매수: `ADD_ON_PYRAMID`
  - 보유 코인 손실 구간 반등 확인형 추가매수: `ADD_ON_AVERAGE_DOWN`
  - 매수 부적합: `HOLD`
- `ADD_ON_PYRAMID`는 수익 구간이거나 돌파 지속 중이며 거래대금 유지 확인이 있을 때만 선택하세요
- `ADD_ON_AVERAGE_DOWN`은 RSI/모멘텀 반전 + 거래대금 확인 + 지지선 재회복이 동시에 있을 때만 선택하세요. 단순 낙폭만으로는 선택하지 마세요
- 실행 계약: 이 경로는 신규 매수 기회 탐색 전용이므로 최종 recommendation은 BUY 또는 HOLD만 사용하고 SELL은 사용하지 마세요
- BTC 도미넌스·알트시즌·섹터 정보가 주어지면 적극 활용하되, 데이터에 없으면 추측하지 마세요
- **절대 규칙**: 목표가/손절가는 반드시 위 현재가/일봉/분봉 데이터에서 도출할 것. 임의의 가격을 만들지 마세요
- 반드시 한국어로 답변"""
CRYPTO_ANALYSIS_SYSTEM = (
    CRYPTO_ANALYSIS_SYSTEM
    .replace("__CRYPTO_MOMENTUM_RR__", f"{CRYPTO_MOMENTUM_RR_FLOOR:.1f}")
    .replace("__CRYPTO_DEFENSIVE_RR__", f"{CRYPTO_DEFENSIVE_RR_FLOOR:.1f}")
)

CRYPTO_ANALYSIS_PROMPT = """## 코인 분석 요청: {stock_name} ({symbol})

### 시장 전체 상황
{market_context}

### 트레이딩 상황
{trading_context}

### 현재 코인 포지션
{current_position_context}

### 계좌 상태
{account_context}

### 현재가 정보
- 시장/통화: {market} / {currency}
- 현재가: {current_price_text}
- 24h 대비: {change_text} ({change_rate:+.2f}%)
- 24h 거래량: {volume:,}
- 24h 거래대금: {trade_value_text}

### 기술적 지표
{technical_indicators}

### 차트 패턴
{chart_patterns}

### 추세 분석 요약
{daily_data}

### 과거 매매 성과 (AI 피드백)
{feedback_context}

---

## 분석 요청
위 데이터를 기반으로 **단계별 사고(Step 1~4)**를 수행한 뒤 최종 판단하세요.

**주의**: 아래 JSON은 필드 구조 설명입니다. target_price, stop_loss_price 등 모든 가격은 반드시 KRW 기준으로, 위 현재가/일봉 데이터를 분석하여 도출하세요.
- recommendation: BUY 또는 HOLD만 사용하세요. SELL은 사용하지 마세요.
- confidence: 이 매매가 손절 전에 목표가에 도달할 확률 (0.00~1.00)
- position_intent: NEW / ADD_ON_PYRAMID / ADD_ON_AVERAGE_DOWN / HOLD
- 거래대금이 약하거나 추격 매수 성격이 강하면 BUY보다 HOLD를 우선하세요

JSON 형식으로 답변:
```json
{{
  "analysis": "시장 주도축·추세·거래대금·리스크보상·피드백을 종합한 분석 (3~4줄)",
  "recommendation": "BUY/HOLD",
  "position_intent": "NEW/ADD_ON_PYRAMID/ADD_ON_AVERAGE_DOWN/HOLD",
  "confidence": 0.00,
  "reason": "최종 판단 이유 (2~3줄)",
  "target_price": 0,
  "stop_loss_price": 0,
  "trailing_stop_pct": 0.0,
  "key_factors": ["위 분석에서 도출한 근거"]
}}
```"""


# ---------------------------------------------------------------------------
# Dispatch 함수
# ---------------------------------------------------------------------------

def get_stock_analysis_system(market: str) -> str:
    """시장별 Tier1 시스템 프롬프트 반환"""
    market_code = normalize_market(market)
    if is_crypto_market(market_code):
        return CRYPTO_ANALYSIS_SYSTEM
    return STOCK_ANALYSIS_SYSTEM


def get_stock_analysis_prompt(market: str) -> str:
    """시장별 Tier1 사용자 프롬프트 템플릿 반환"""
    market_code = normalize_market(market)
    if is_crypto_market(market_code):
        return CRYPTO_ANALYSIS_PROMPT
    return STOCK_ANALYSIS_PROMPT
