# 내 자산 (myAsset)

**채널:** Private (JWT 인증 필요)
**type:** `myAsset`
**설명:** 내 자산(잔고)의 변동을 실시간으로 수신한다. 주문 체결, 입출금 등으로 잔고가 변경될 때 메시지가 전송된다.

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
  {"type": "myAsset"}
]
```

### 요청 파라미터

| 필드 | 타입 | 필수 | 설명 | 기본값 |
|------|------|------|------|--------|
| type | String | O | `"myAsset"` | — |

`codes` 필드 불필요. 전체 보유 자산의 변동을 자동 수신.

## 응답 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| type | ty | String | `"myAsset"` |
| assets | ast | List[Object] | 자산 목록 |
| asset_timestamp | asttms | Long | 자산 상태 타임스탬프 (밀리초) |
| timestamp | tms | Long | 응답 타임스탬프 (밀리초) |
| stream_type | st | String | `"REALTIME"` |

### assets 내부 필드

| 필드 (DEFAULT) | 축약 (SIMPLE) | 타입 | 설명 |
|----------------|--------------|------|------|
| currency | cu | String | 화폐 코드 (대문자, 예: KRW, BTC, ETH) |
| balance | b | String | 거래 가능 잔고 |
| locked | l | String | 주문 등으로 잠긴 수량 |

## 응답 예시

```json
{
  "type": "myAsset",
  "assets": [
    {
      "currency": "KRW",
      "balance": "2061832.35",
      "locked": "3824127.3"
    }
  ],
  "asset_timestamp": 1727052537592,
  "timestamp": 1727052537687,
  "stream_type": "REALTIME"
}
```

## 설명

- `balance`와 `locked`는 문서상 Double이나 **실제 응답은 String 타입**으로 반환됨 (파싱 시 주의)
- 주문 체결, 입출금 등 잔고 변동 시 변동된 화폐의 자산 정보가 전송됨
- REST API의 `GET /v1/accounts` 폴링 대신 실시간 잔고 추적에 사용 가능
- Private 채널이므로 `myOrder`와 동일한 WebSocket 연결에서 함께 구독 가능:

```json
[
  {"ticket": "private-ticket"},
  {"type": "myOrder", "codes": []},
  {"type": "myAsset"}
]
```
