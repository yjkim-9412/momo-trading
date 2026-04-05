# 코인 ExitPlan 후속 작업 문서

## 이번 적용 범위

- 코인 `BUY` 체결 후 `DecisionMaker.confirm_and_record()` 경로에서 공통 `ExitPlan`을 실제로 생성/갱신하도록 연결했다.
- 코인 `BUY` 기록은 `exit_levels`, `trade_threshold_payload`, `trailing_stop_pct`, `exit_reasoning`을 `notes`에 함께 남긴다.
- 체결 직후 `exit_plan_id`를 `event_detector`에 전달할 수 있도록 코인 `trade_result` 경로가 메타데이터를 반환한다.
- 공통 `exit_plans.total_quantity` / `exit_plan_history.total_quantity`는 코인 소수 수량을 위해 `float`로 확장했다.
- 부분 익절 수량 계산은 시장별 수량 정책을 따르도록 바꿨다.
  - 주식: 기존 정수 semantics 유지
  - 코인: 8자리 소수점 floor 유지
- 코인 부분청산 시 `coin_trade_results`가 남은 포지션을 계속 열어 두고, 실현된 청산분만 별도 완료 거래로 남기도록 보정했다.
- 코인 전량 청산 시 활성 `ExitPlan`은 종목 기준으로 자동 비활성화된다.

## 현재 동작 방식

1. 코인 Tier2 분석이 `stop_loss_price`, `take_profit_price`, `trailing_stop_pct`를 주면 런타임이 `trade_threshold_payload`를 만든다.
2. `BUY` 체결 후 `DecisionMaker`가 코인 `trade_result`를 기록하고 공통 `ExitPlan`을 생성한다.
3. 생성된 `exit_plan_id`와 TP/SL 레벨이 `event_detector`에 활성화된다.
4. 재시작 시 DB의 활성 `ExitPlan`이 다시 로드되어 코인 감시가 복원된다.

## 이번 릴리즈에서 의도적으로 미룬 항목

- 코인 Tier1/Tier2 프롬프트에 `exit_levels` 다단계 작성을 강제하는 작업
  - 현재는 `take_profit_price`만 있어도 단일 TP fallback으로 `ExitPlan`이 동작한다.
- 빗각(Oblique Angle) 규칙 엔진 도입
  - 이번 작업은 `ExitPlan` 연결 안정화가 우선이다.
- DTW, EMA 미분, walk-forward 자동 최적화
  - 연구 후보군으로 보관하고 실거래 경로에는 아직 넣지 않는다.

## 다음 권장 작업

### 1. 코인 프롬프트 다단계 익절 고도화

- `analysis/llm/prompts/stock_analysis.py`의 코인 프롬프트에 `exit_levels`, `exit_reasoning`을 추가
- `analysis/llm/prompts/final_review.py`의 코인 Tier2에도 동일 계약을 추가
- 운영 초기에는 `최대 2개 레벨`까지만 허용

### 2. 빗각 규칙 엔진 Shadow 모드

- `analysis/technical/oblique_angle.py` 추가
- 피벗 기반 하락 저항선, 정규화 각도, 거래대금 배수, 윗꼬리 비율을 계산
- 실제 주문 게이트가 아니라 추천/로그만 남기는 `shadow_only` 모드로 먼저 운영

### 3. 코인 ExitPlan 운영 지표 추가

- 활성 plan 수
- plan 생성 성공률
- stop-loss / take-profit / trailing exit 비중
- 부분청산 이후 남은 포지션 평균 보유시간

## 검증 체크리스트

- 코인 `BUY` 체결 후 `event_detector`에 `exit_plan_id`가 설정되는가
- 서버 재시작 후 활성 코인 `ExitPlan`이 복원되는가
- 코인 부분 익절 시 남은 수량이 소수점 손실 없이 유지되는가
- 전량 청산 후 `exit_plans.is_active`가 `false`로 내려가는가
