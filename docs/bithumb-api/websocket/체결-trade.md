# 체결 (trade)

**채널:** Public (인증 불필요)
**type:** `trade`
**설명:** 마켓의 실시간 체결 내역을 수신한다.

## 요청

```json
[
  {"ticket": "test-example"},
  {"type": "trade", "codes": ["KRW-BTC", "KRW-ETH"]},
  {"format": "DEFAULT"}
]
```

### 요청 파라미터

| 필드 | 타입 | 필수 | 설명 | 기본값 |
|------|------|------|------|--------|
| type | String | O | `"trade"` | — |
| codes | List | O | 마켓 코드 목록 (대문자) | — |
| isOnlySnapshot | Boolean | N | 스냅샷만 수신 | false |
| isOnlyRealtime | Boolean | N | 실시간만 수신 | false |

## 응답 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| type | ty | String | `"trade"` |
| code | cd | String | 마켓 코드 (예: KRW-BTC) |
| trade_price | tp | Double | 체결 가격 |
| trade_volume | tv | Double | 체결량 |
| ask_bid | ab | String | 매도/매수 구분: `ASK` / `BID` |
| prev_closing_price | pcp | Double | 전일 종가 |
| change | c | String | 변화 방향: `RISE` / `EVEN` / `FALL` |
| change_price | cp | Double | 부호 없는 변화량 |
| trade_date | tdt | String | 체결 일자 KST (yyyy-MM-dd) |
| trade_time | ttm | String | 체결 시각 KST (HH:mm:ss) |
| trade_timestamp | ttms | Long | 체결 타임스탬프 (밀리초) |
| sequential_id | sid | Long | 체결 고유 번호 |
| timestamp | tms | Long | 응답 타임스탬프 (밀리초) |
| stream_type | st | String | `SNAPSHOT` / `REALTIME` |

## 응답 예시

```json
{
  "type": "trade",
  "code": "KRW-BTC",
  "trade_price": 489700,
  "trade_volume": 1.4825,
  "ask_bid": "BID",
  "prev_closing_price": 484500,
  "change": "RISE",
  "change_price": 5200,
  "trade_date": "2024-09-10",
  "trade_time": "09:58:54",
  "trade_timestamp": 1725929934373,
  "sequential_id": 17259299343730000,
  "timestamp": 1725929934483,
  "stream_type": "REALTIME"
}
```

## 설명

- `sequential_id`는 체결의 고유성을 나타내지만 정렬 순서를 보장하지 않음
- `trade_date`, `trade_time`은 KST 기준
- `change`는 전일 종가 대비 방향
