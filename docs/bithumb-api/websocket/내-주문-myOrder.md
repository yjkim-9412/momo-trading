# 내 주문 (myOrder)

**채널:** Private (JWT 인증 필요)
**type:** `myOrder`
**설명:** 내 주문의 상태 변경을 실시간으로 수신한다. 주문 접수, 체결, 취소 등 상태 변화 시 메시지가 전송된다.

## 연결

Private 엔드포인트에 JWT Bearer 토큰으로 연결:
```
wss://ws-api.bithumb.com/websocket/v1/private
Authorization: Bearer {jwtToken}
```

## 요청

```json
[
  {"ticket": "test-example"},
  {"type": "myOrder", "codes": ["KRW-BTC"]}
]
```

### 전체 마켓 구독 (codes 비워두기)

```json
[
  {"ticket": "test-example"},
  {"type": "myOrder", "codes": []}
]
```

### 요청 파라미터

| 필드 | 타입 | 필수 | 설명 | 기본값 |
|------|------|------|------|--------|
| type | String | O | `"myOrder"` | — |
| codes | List | N | 마켓 코드 목록 (대문자). 빈 배열이면 전체 마켓 | `[]` |

## 응답 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| type | ty | String | `"myOrder"` |
| code | cd | String | 마켓 코드 (예: KRW-BTC) |
| client_order_id | coid | String | 사용자 지정 주문 식별자 |
| uuid | uid | String | 시스템 주문 고유 ID |
| ask_bid | ab | String | 매도/매수: `ASK` / `BID` |
| order_type | ot | String | 주문 유형 (아래 참조) |
| state | s | String | 주문 상태 (아래 참조) |
| trade_uuid | tuid | String | 체결 고유 ID |
| price | p | Double | 주문/체결 가격 |
| volume | v | Double | 주문/체결 수량 |
| remaining_volume | rv | Double | 미체결 수량 |
| executed_volume | ev | Double | 체결 수량 |
| trades_count | tc | Double | 해당 주문의 체결 횟수 |
| reserved_fee | rsf | Double | 예약 수수료 |
| remaining_fee | rmf | Double | 잔여 수수료 |
| paid_fee | pf | Double | 지불 수수료 |
| executed_funds | ef | Double | 총 체결 금액 |
| trade_timestamp | ttms | Long | 체결 타임스탬프 (밀리초) |
| order_timestamp | otms | Long | 주문 생성 타임스탬프 (밀리초) |
| timestamp | tms | Long | 응답 타임스탬프 (밀리초) |
| stream_type | st | String | `"REALTIME"` |

### order_type 값

| 값 | 설명 |
|----|------|
| `limit` | 지정가 주문 |
| `price` | 시장가 매수 (총액 지정) |
| `market` | 시장가 매도 (수량 지정) |

### state 값

| 값 | 설명 |
|----|------|
| `wait` | 대기 (미체결) |
| `trade` | 부분 체결 |
| `done` | 전량 체결 완료 |
| `cancel` | 취소 |

## 응답 예시

```json
{
  "type": "myOrder",
  "code": "KRW-BTC",
  "client_order_id": "my-client-order-id-1",
  "uuid": "C0101000000001818113",
  "ask_bid": "BID",
  "order_type": "limit",
  "state": "trade",
  "trade_uuid": "C0101000000001744207",
  "price": 1927000,
  "volume": 0.4697,
  "remaining_volume": 0.0803,
  "executed_volume": 0.4697,
  "trades_count": 1,
  "reserved_fee": 0,
  "remaining_fee": 0,
  "paid_fee": 0,
  "executed_funds": 905111.9000,
  "trade_timestamp": 1727052318148,
  "order_timestamp": 1727052318074,
  "timestamp": 1727052318369,
  "stream_type": "REALTIME"
}
```

## 설명

- 주문 접수, 부분 체결, 전량 체결, 취소 시마다 메시지 수신
- `codes`를 비워두면 모든 마켓의 내 주문 상태를 수신
- `client_order_id`는 주문 시 사용자가 지정한 값 (REST API `POST /v1/orders`의 `client_order_id` 파라미터)
- `state`가 `trade`일 때 `remaining_volume > 0`이면 부분 체결 상태
- REST API의 `GET /v1/order` 폴링 대신 실시간 상태 추적에 사용 가능
