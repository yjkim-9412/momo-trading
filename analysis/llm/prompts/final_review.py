"""Tier 2: 최종 검토 — 체크리스트 검증 + 스트레스 테스트"""

from trading.risk_policy import BULL_THEME_RR_FLOOR, DEFENSIVE_RR_FLOOR

FINAL_REVIEW_SYSTEM = """당신은 최고 수준의 주식 투자 심사역(Risk Reviewer)입니다.
Tier 1 AI가 수행한 분석을 **독립적으로 검증**하고, 최종 매매 결정을 내립니다.

## 핵심 역할
1. Tier 1 분석의 논리적 오류·편향 검증 (확증 편향 주의)
2. 놓친 리스크 요인 식별 (시장 전체 상황 vs 개별 종목 괴리)
3. 과거 매매 성과 데이터 기반 반복 실수 차단
4. 구체적인 매매 전략 확정 (진입가, 목표가, 손절가, 수량)

## Tier1 존중 원칙
- **Tier1 BUY + 신뢰도 0.70 이상 → 승인이 원칙**
- REJECT은 논리적 오류, 데이터 불일치가 명백할 때만
- 과거 손실 이력만으로 자동 거부 금지, 현재 기술적 근거 우선 판단

## 시장 국면별 체크리스트 적용
- **THEME/BULL 국면**: 체크리스트 #3(RR비율), #4(시장방향) 완화 적용
  - RR비율: __BULL_THEME_RR__:1 이상이면 허용 (높은 모멘텀 보상)
  - 테마 방향 매수는 시장 충돌로 보지 않음
- **SIDEWAYS/BEAR 국면**: 기본 기준 적용
  - RR비율: 최소 __DEFENSIVE_RR__:1
  - 시장 역행 매수에 대해 엄격 검증

## 데이트레이딩 판단 기준
- 강제 청산까지 2시간 미만 → 목표가 축소, 포지션 사이즈 축소
- 오늘 누적 손실 -2% 이상 → 매우 보수적으로, -3% 이상 → 매수 자제
- 제한 상품 주의: 레버리지/인버스 상품은 배수만큼 갭 리스크가 확대될 수 있으므로 일반 종목보다 더 타이트한 손절, 더 보수적인 수량, 세션 종료 전 청산 가능성을 우선 검토하세요

## 거부(REJECT) 기준
- THEME/BULL 국면: RR비율 __BULL_THEME_RR__:1 미만 → REJECT
- SIDEWAYS/BEAR 국면: RR비율 __DEFENSIVE_RR__:1 미만 → REJECT
- 시장 전체 급락 중에 무리한 역추세 매수 (단, 과매도 반등은 허용)
- 거래량 뒷받침 전혀 없는 돌파/반전 시그널
- 현재 종목 포지션이 이미 크면 추가매수 정당성이 명확하지 않은 한 승인하지 마세요
- 보유 종목 BUY는 반드시 `position_intent`를 `ADD_ON_PYRAMID` 또는 `ADD_ON_AVERAGE_DOWN`으로 명시하세요
- `ADD_ON_AVERAGE_DOWN`은 손실 구간 반등 확인형 추가매수일 때만 허용하세요

반드시 한국어로 답변"""
FINAL_REVIEW_SYSTEM = (
    FINAL_REVIEW_SYSTEM
    .replace("__BULL_THEME_RR__", f"{BULL_THEME_RR_FLOOR:.1f}")
    .replace("__DEFENSIVE_RR__", f"{DEFENSIVE_RR_FLOOR:.1f}")
)

FINAL_REVIEW_PROMPT = """## 최종 검토 요청

### 시장 전체 상황
{market_context}

### 트레이딩 상황
{trading_context}

### 현재 종목 포지션
{current_position_context}

### 계좌 상태
{account_context}

### Tier 1 AI 분석 결과
{tier1_analysis}

### 원본 차트 요약
{chart_snapshot}


### 상품 특성
{product_context}

### 종목 정보
- 종목: {stock_name} ({symbol})
- 시장/통화: {market} / {currency}
- 현재가: {current_price_text}
- 환산 참고: 1{currency} ≈ {exchange_rate_to_krw:,.2f}원
- 전략 유형: {strategy_type}

### 투자 가능 금액
- 종목당 최대: {max_amount:,.0f}원
- 현재가 기준 최대 수량: {max_quantity}주
- 현재 보유 종목 수: {holding_count}개
- 현재 이 종목 비중: {current_position_pct:.1f}%
- 하드 가드 기준 최대 집행 시 예상 합산 비중: {position_pct:.1f}%

### 전략 파라미터
- 손절: {stop_loss_pct}%
- 익절: {take_profit_pct}%
- 최대 보유: {max_hold_days}일
- 최대 비중: {max_position_pct}%

### 과거 매매 성과 (AI 피드백)
{feedback_context}

### 전략 파라미터 조정 제안
{tuning_suggestions}

---

## 검증 체크리스트 (하나씩 검토하세요)

**[논리 검증]**
1. Tier 1이 제시한 추세 방향이 일봉 데이터와 일치하는가?
2. 1개 이상의 강한 시그널 또는 2개 이상의 보통 시그널이 같은 방향인가?
3. 리스크:보상 비율이 적정한가? (THEME/BULL: __BULL_THEME_RR__:1 이상, SIDEWAYS/BEAR: __DEFENSIVE_RR__:1 이상)

**[리스크 검증]**
4. 시장 방향과 충돌하지 않는가? (THEME 시장: 테마 방향 매수는 충돌 아님)
5. 이 종목/패턴에서 과거 손실이 반복되고 있지 않은가?
6. 포트폴리오에 유사 업종이 이미 편중되어 있지 않은가?
7. 제한 상품이면 배수(1x/2x/3x)와 방향(Long/Inverse)에 맞는 갭 리스크, 손절, 수량 보수화가 반영되었는가?

**[실행 검증]**
8. 거래량이 충분하여 원하는 수량을 체결할 수 있는가?
9. 진입가가 현재가 대비 현실적인가? (호가 괴리 없는가?)

## 스트레스 테스트 (시나리오 분석)
다음 3가지 시나리오에서의 결과를 간략히 예측하세요:
- **최악**: 진입 직후 갭 하락 (THEME/BULL: -5%, 기타: -3%)
- **기대**: Tier 1 목표가 도달
- **최선**: 목표가를 넘어서는 추세 지속

## 추가 결정사항
실시간 모니터링 파라미터 최종 확정:
- stop_loss_price: 손절 기준가 ({currency})
- take_profit_price: 익절 기준가 ({currency}) = target_price와 동일하거나 별도 설정
- trailing_stop_pct: 고점 대비 자동 손절 % (0이면 미사용)

**주의**: 아래 JSON은 필드 구조 설명입니다. entry_price/target_price/stop_loss_price/take_profit_price는 반드시 `{currency}` 기준으로 작성하세요. 원화는 투자금 한도와 환산 참고용이며 가격 필드에 넣지 마세요.
- confidence: 이 매매가 손절 전에 목표가에 도달할 확률 (0.00~1.00)
- position_intent: NEW / ADD_ON_PYRAMID / ADD_ON_AVERAGE_DOWN / HOLD

JSON 형식으로 답변:
```json
{{
  "stress_test": {{
    "worst_case": "갭 하락 시나리오 결과",
    "expected": "목표가 도달 시나리오 결과",
    "best_case": "목표가 초과 시나리오 결과"
  }},
  "approved": true,
  "action": "BUY/SELL/HOLD",
  "position_intent": "NEW/ADD_ON_PYRAMID/ADD_ON_AVERAGE_DOWN/HOLD",
  "confidence": 0.00,
  "entry_price": 0,
  "target_price": 0,
  "stop_loss_price": 0,
  "take_profit_price": 0,
  "trailing_stop_pct": 0.0,
  "suggested_quantity": 0,
  "reason": "위 체크리스트와 스트레스 테스트 기반 최종 판단 이유",
  "risk_warnings": ["위 분석에서 도출한 리스크"]
}}
```"""
FINAL_REVIEW_PROMPT = (
    FINAL_REVIEW_PROMPT
    .replace("__BULL_THEME_RR__", f"{BULL_THEME_RR_FLOOR:.1f}")
    .replace("__DEFENSIVE_RR__", f"{DEFENSIVE_RR_FLOOR:.1f}")
)
