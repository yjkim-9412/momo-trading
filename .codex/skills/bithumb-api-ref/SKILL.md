---
name: bithumb-api-ref
description: 빗썸 거래소 API 레퍼런스 조회. 빗썸 REST API(PUBLIC 9개 + PRIVATE 25개), WebSocket API(PUBLIC 3개 + PRIVATE 2개), JWT 인증, rate limit, 에러 코드 참조 시 사용.
---

# Bithumb API 레퍼런스 조회

빗썸 거래소 API 참조가 필요할 때 로컬 레퍼런스를 조회한다. REST API(PUBLIC 9개 + PRIVATE 25개)와 WebSocket API(PUBLIC 3개 + PRIVATE 2개)를 포함한다.

**공식 문서:** https://apidocs.bithumb.com/v2.1.0/reference/

## 레퍼런스 구조

```
docs/bithumb-api/
├── README.md          # 전체 인덱스 (카테고리별 엔드포인트 + 키워드)
├── public/            # PUBLIC API (인증 불필요, 9개)
│   ├── 마켓코드-조회.md
│   ├── 분minute-캔들.md
│   ├── 일day-캔들.md
│   ├── 주week-캔들.md
│   ├── 월month-캔들.md
│   ├── 최근-체결-내역.md
│   ├── 현재가-정보.md
│   ├── 호가-정보-조회.md
│   └── 경보제.md
├── private/           # PRIVATE API (JWT 인증 필요, 25개)
│   ├── 전체-계좌-조회.md
│   ├── 주문-가능-정보.md
│   ├── 개별-주문-조회.md
│   ├── 주문-리스트-조회.md
│   ├── 주문-취소-접수.md
│   ├── 주문하기.md
│   ├── twap-주문내역-조회.md
│   ├── twap-주문-취소.md
│   ├── twap-주문-요청.md
│   ├── 출금-리스트-조회.md
│   ├── 원화-출금-리스트-조회.md
│   ├── 개별-출금-조회.md
│   ├── 출금-가능-정보.md
│   ├── 디지털-자산-출금하기.md
│   ├── 원화-출금하기.md
│   ├── 출금-허용-주소-리스트-조회.md
│   ├── 입금-리스트-조회.md
│   ├── 원화-입금-리스트-조회.md
│   ├── 개별-입금-조회.md
│   ├── 입금-주소-생성-요청.md
│   ├── 전체-입금-주소-조회.md
│   ├── 개별-입금-주소-조회.md
│   ├── 원화-입금하기.md
│   ├── 입출금-현황.md
│   └── api-키-리스트-조회.md
└── websocket/         # WEBSOCKET API (Public 3 + Private 2)
    ├── 기본-정보.md        # 연결, 인증, 요청 포맷, rate limit, 에러 코드
    ├── 현재가-ticker.md    # Public: 현재가 실시간 (32개 필드)
    ├── 체결-trade.md       # Public: 체결 내역 (14개 필드)
    ├── 호가-orderbook.md   # Public: 호가 + depth aggregation
    ├── 내-주문-myOrder.md  # Private: 내 주문 상태 변경 (20개 필드)
    └── 내-자산-myAsset.md  # Private: 내 자산 변동
```

## 주요 API 카테고리

| 카테고리 | 유형 | 핵심 API |
|---------|------|---------|
| 시세 종목 | PUBLIC | 마켓코드조회 (`/v1/market/all`) |
| 시세 캔들 | PUBLIC | 분/일/주/월 캔들 (`/v1/candles/*`) |
| 시세 체결 | PUBLIC | 최근체결내역 (`/v1/trades/ticks`) |
| 현재가 | PUBLIC | 현재가정보 (`/v1/ticker`) |
| 호가 | PUBLIC | 호가정보 (`/v1/orderbook`) |
| 자산 | PRIVATE | 전체계좌조회 (`/v1/accounts`) |
| 주문 | PRIVATE | 주문하기/조회/취소 (`/v1/orders`, `/v1/order`) |
| TWAP | PRIVATE | TWAP 주문/조회/취소 (`/v1/twap/*`) |
| 출금 | PRIVATE | 코인·원화 출금 (`/v1/withdraws/*`) |
| 입금 | PRIVATE | 코인·원화 입금 (`/v1/deposits/*`) |
| 서비스 | PRIVATE | 입출금현황, API키 조회 |
| WebSocket 기본 | WS | 연결, 인증, 요청 포맷 (`wss://ws-api.bithumb.com/websocket/v1`) |
| WS 현재가 | WS PUBLIC | ticker 실시간 (`type: "ticker"`) |
| WS 체결 | WS PUBLIC | trade 실시간 (`type: "trade"`) |
| WS 호가 | WS PUBLIC | orderbook 실시간 + depth aggregation (`type: "orderbook"`) |
| WS 내주문 | WS PRIVATE | myOrder 주문 상태 변경 (`type: "myOrder"`) |
| WS 내자산 | WS PRIVATE | myAsset 잔고 변동 (`type: "myAsset"`) |

## 빗썸 WebSocket API

- **Public**: `wss://ws-api.bithumb.com/websocket/v1` (인증 불필요)
- **Private**: `wss://ws-api.bithumb.com/websocket/v1/private` (JWT Bearer)
- **구독 5종**: `ticker` / `trade` / `orderbook` (Public), `myOrder` / `myAsset` (Private)
- **요청 형식**: JSON 배열 `[{ticket}, {type}, ..., {type}, {format}]`
- **연결 제한**: 10 conn/sec/IP, 메시지 5/sec + 100/min, 수신 무제한
- **연결 유지**: RFC 6455 PING/PONG, 120초 idle timeout, 서버 `{"status":"UP"}` 매 10초
- **에러 코드**: `WRONG_FORMAT`, `NO_TICKET`, `NO_TYPE`, `NO_CODES`, `INVALID_PARAM`
- **상세 문서**: `docs/bithumb-api/websocket/` (6개 파일)

## 빗썸 API 요청 제한 (v2.1.0)

| 구분 | 공식 한도 | 코드 설정 (보수적) | 비고 |
|------|----------|------------------|------|
| Public API | 150 req/s | 10 req/s (semaphore) | 시세, 캔들, 호가 |
| Private API | 140 req/s | 5 req/s (semaphore) | 잔고, 입출금 |
| 주문 (생성/취소) | 10 req/s | 별도 제한 없음 | 초과 시 일시 제한 |

- 초과 시: API 사용 일시 제한 → 대기 후 재요청
- 과도한 트래픽 시 별도 공지 없이 제한값 조정 가능
- 요청 간격을 균등 분산하면 제한 가능성 감소

## 빗썸 JWT 인증 구조

```
Authorization: Bearer {jwt_token}
Content-Type: application/json; charset=utf-8
```

**JWT Payload:**
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `access_key` | String | O | API Key |
| `nonce` | String | O | UUID v4 |
| `timestamp` | Number | O | 밀리초 단위 |
| `query_hash` | String | 파라미터 있을 때 | SHA-512 해시 of query string |
| `query_hash_alg` | String | query_hash 있을 때 | `"SHA512"` |

서명: **HS256** (Secret Key로 서명)

## 빗썸 주요 에러 코드

| HTTP | 코드 | 설명 |
|------|------|------|
| 400 | `invalid_parameter` | 잘못된 파라미터 |
| 400 | `invalid_price` | 주문가격 단위 오류 |
| 400 | `cross_trading` | 기존 주문과 체결 가능성으로 취소 |
| 401 | `jwt_verification` | JWT 토큰 검증 실패 |
| 401 | `expired_jwt` | JWT 만료 |
| 401 | `NotAllowIP` | 허용되지 않는 IP |
| 401 | `out_of_scope` | API 권한 부족 |
| 404 | `order_not_found` | 주문 정보 없음 |
| 422 | `order_not_ready` | 주문 처리 중, 재시도 필요 |
| 500 | `server_error` | 서버 오류, 재시도 |

## 조회 절차

키워드에 대해 아래 순서로 조회:

1. `docs/bithumb-api/README.md`를 읽어서 매칭되는 엔드포인트를 찾는다
2. 매칭된 엔드포인트 상세 파일(`docs/bithumb-api/{public|private|websocket}/`)을 읽는다
3. API URL, 인증, 요청/응답 스키마, 프로젝트 코드와의 연관성을 정리한다
