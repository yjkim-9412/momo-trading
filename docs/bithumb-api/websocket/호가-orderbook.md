# 호가 (orderbook)

**채널:** Public (인증 불필요)
**type:** `orderbook`
**설명:** 마켓의 실시간 호가 정보를 수신한다. `level` 파라미터로 가격 묶음 단위를 지정할 수 있다.

## 요청

```json
[
  {"ticket": "test-example"},
  {"type": "orderbook", "codes": ["KRW-BTC", "KRW-ETH"], "level": 10},
  {"format": "DEFAULT"}
]
```

### 종목별 다른 level 지정

```json
[
  {"ticket": "test-example"},
  {"type": "orderbook", "codes": ["KRW-BTC"], "level": 1000},
  {"type": "orderbook", "codes": ["KRW-XRP"], "level": 1},
  {"format": "DEFAULT"}
]
```

### 요청 파라미터

| 필드 | 타입 | 필수 | 설명 | 기본값 |
|------|------|------|------|--------|
| type | String | O | `"orderbook"` | — |
| codes | List | O | 마켓 코드 목록 (대문자) | — |
| level | Double | N | 가격 묶음 단위 (depth aggregation) | 1 |
| isOnlySnapshot | Boolean | N | 스냅샷만 수신 | false |
| isOnlyRealtime | Boolean | N | 실시간만 수신 | false |

## 응답 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| type | ty | String | `"orderbook"` |
| code | cd | String | 마켓 코드 (예: KRW-BTC) |
| total_ask_size | tas | Double | 총 매도 잔량 |
| total_bid_size | tbs | Double | 총 매수 잔량 |
| orderbook_units | obu | List[Object] | 호가 단위 리스트 |
| level | lv | Double | 묶음 단위 |
| timestamp | tms | Long | 응답 타임스탬프 (밀리초) |
| stream_type | st | String | `SNAPSHOT` / `REALTIME` |

### orderbook_units 내부 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| ask_price | ap | Double | 매도 호가 |
| bid_price | bp | Double | 매수 호가 |
| ask_size | as | Double | 매도 잔량 |
| bid_size | bs | Double | 매수 잔량 |

## 응답 예시

```json
{
  "type": "orderbook",
  "code": "KRW-BTC",
  "total_ask_size": 450.3526,
  "total_bid_size": 63.3006,
  "orderbook_units": [
    {
      "ask_price": 478800,
      "bid_price": 478300,
      "ask_size": 4.3478,
      "bid_size": 5.6370
    },
    {
      "ask_price": 489700,
      "bid_price": 477900,
      "ask_size": 2.3642,
      "bid_size": 0.9705
    }
  ],
  "level": 1,
  "timestamp": 1725930007672,
  "stream_type": "REALTIME"
}
```

## 설명

- `level`을 크게 설정하면 호가가 해당 단위로 묶여서 제공됨 (depth aggregation)
  - 예: `level: 1000`이면 1,000원 단위로 호가 그룹화
  - 예: `level: 1`이면 최소 호가 단위 (기본값)
- `orderbook_units`는 매도호가(ask) 오름차순, 매수호가(bid) 내림차순으로 정렬
- REST API의 호가 조회와 동일한 구조이나, 실시간 스트리밍으로 제공
