# 현재가 (ticker)

**채널:** Public (인증 불필요)
**type:** `ticker`
**설명:** 마켓의 현재가 실시간 정보를 수신한다.

## 요청

```json
[
  {"ticket": "test-example"},
  {"type": "ticker", "codes": ["KRW-BTC", "KRW-ETH"]},
  {"format": "DEFAULT"}
]
```

### 요청 파라미터

| 필드 | 타입 | 필수 | 설명 | 기본값 |
|------|------|------|------|--------|
| type | String | O | `"ticker"` | — |
| codes | List | O | 마켓 코드 목록 (대문자) | — |
| isOnlySnapshot | Boolean | N | 스냅샷만 수신 | false |
| isOnlyRealtime | Boolean | N | 실시간만 수신 | false |

## 응답 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| type | ty | String | `"ticker"` |
| code | cd | String | 마켓 코드 (예: KRW-BTC) |
| opening_price | op | Double | 시가 |
| high_price | hp | Double | 고가 |
| low_price | lp | Double | 저가 |
| trade_price | tp | Double | 현재가 (최근 체결가) |
| prev_closing_price | pcp | Double | 전일 종가 |
| change | c | String | 변화 방향: `RISE` / `EVEN` / `FALL` |
| change_price | cp | Double | 부호 없는 변화량 |
| signed_change_price | scp | Double | 부호 있는 변화량 |
| change_rate | cr | Double | 부호 없는 변화율 |
| signed_change_rate | scr | Double | 부호 있는 변화율 |
| trade_volume | tv | Double | 최근 체결량 |
| acc_trade_volume | atv | Double | 누적 거래량 (KST 00:00 기준) |
| acc_trade_volume_24h | atv24h | Double | 24시간 누적 거래량 |
| acc_trade_price | atp | Double | 누적 거래대금 (KST 00:00 기준) |
| acc_trade_price_24h | atp24h | Double | 24시간 누적 거래대금 |
| trade_date | tdt | String | 체결 일자 (yyyyMMdd) |
| trade_time | ttm | String | 체결 시각 (HHmmss) |
| trade_timestamp | ttms | Long | 체결 타임스탬프 (밀리초) |
| ask_bid | ab | String | 매도/매수 구분: `ASK` / `BID` |
| acc_ask_volume | aav | Double | 누적 매도량 |
| acc_bid_volume | abv | Double | 누적 매수량 |
| highest_52_week_price | h52wp | Double | 52주 최고가 |
| highest_52_week_date | h52wdt | String | 52주 최고가 달성일 (yyyy-MM-dd) |
| lowest_52_week_price | l52wp | Double | 52주 최저가 |
| lowest_52_week_date | l52wdt | String | 52주 최저가 달성일 (yyyy-MM-dd) |
| market_state | ms | String | 거래 상태 (예: `ACTIVE`) |
| is_trading_suspended | its | Boolean | 거래 정지 여부 |
| delisting_date | dd | Date | 상장폐지일 (nullable) |
| market_warning | mw | String | 유의 종목 여부: `NONE` / `CAUTION` |
| timestamp | tms | Long | 응답 타임스탬프 (밀리초) |
| stream_type | st | String | `SNAPSHOT` / `REALTIME` |

## 응답 예시

```json
{
  "type": "ticker",
  "code": "KRW-BTC",
  "opening_price": 484500,
  "high_price": 493100,
  "low_price": 472500,
  "trade_price": 493100,
  "prev_closing_price": 484500,
  "change": "RISE",
  "change_price": 8600,
  "signed_change_price": 8600,
  "change_rate": 0.01775026,
  "signed_change_rate": 0.01775026,
  "trade_volume": 1.2567,
  "acc_trade_volume": 225.622,
  "acc_trade_volume_24h": 13386.15417512,
  "acc_trade_price": 108663718.238256,
  "acc_trade_price_24h": 8230696760.346009,
  "trade_date": "20240910",
  "trade_time": "091617",
  "trade_timestamp": 1725927377820,
  "ask_bid": "BID",
  "acc_ask_volume": 106.7561,
  "acc_bid_volume": 118.8659,
  "highest_52_week_price": 999999000,
  "highest_52_week_date": "2024-06-18",
  "lowest_52_week_price": 1000,
  "lowest_52_week_date": "2024-06-18",
  "market_state": "ACTIVE",
  "is_trading_suspended": false,
  "delisting_date": null,
  "market_warning": "NONE",
  "timestamp": 1725927377931,
  "stream_type": "REALTIME"
}
```

## 설명

- `stream_type`이 `SNAPSHOT`이면 구독 직후의 스냅샷, `REALTIME`이면 이후 실시간 변경분
- `change` 필드는 전일 종가 대비 방향 (REST API의 ticker와 동일)
- `acc_trade_*` 누적 값은 KST 00:00 기준 / 24h 기준 두 가지 제공
