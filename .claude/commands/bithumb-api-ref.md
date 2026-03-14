# Bithumb API 레퍼런스 조회

빗썸 거래소 API 참조가 필요할 때 로컬 레퍼런스를 조회한다. REST API(PUBLIC 9개 + PRIVATE 25개)와 WebSocket API(PUBLIC 3개 + PRIVATE 2개)를 포함한다.

**공식 문서:** https://apidocs.bithumb.com/v2.1.0/reference/

## 사용자 인자

$ARGUMENTS — 검색할 API 기능 키워드 (예: 주문, 시세, 캔들, 출금, ticker, orderbook 등)

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

## 실행 절차

사용자 인자 "$ARGUMENTS"에 대해 아래 순서로 조회:

### Step 1: 인덱스에서 키워드 검색

`docs/bithumb-api/README.md`를 읽어서 "$ARGUMENTS"와 매칭되는 엔드포인트를 찾는다.
한글·영문 키워드 모두 매칭 대상이다.

### Step 2: 매칭된 엔드포인트 상세 조회

매칭된 엔드포인트의 상세 파일을 읽는다:
```
docs/bithumb-api/{public|private|websocket}/{엔드포인트}.md
```

WebSocket 관련 키워드(websocket, 웹소켓, 실시간, realtime, stream, ticker, trade, orderbook, myOrder, myAsset)는 `websocket/` 폴더에서 조회한다.

여러 개가 매칭되면 가장 관련도 높은 1~3개를 선택한다.

### Step 3: 결과 정리

조회한 내용을 바탕으로:
- API URL, HTTP 메서드, 인증 요구사항 정리
- 요청 파라미터 & 응답 스키마 설명
- 코드 예제 (Python, JavaScript, Java)
- 현재 프로젝트(MOMO Trading) 코드와의 연관성 안내
