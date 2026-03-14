# Bithumb API v2.1.0 레퍼런스 인덱스

공식 문서: https://apidocs.bithumb.com/v2.1.0/reference/

Base URL: `https://api.bithumb.com`

---

## PUBLIC API (인증 불필요)

### 시세 종목 조회
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 마켓 코드 조회 | GET | `/v1/market/all` | `public/마켓코드-조회.md` | market, 종목, 마켓, 코인목록, symbol |

### 시세 캔들 조회
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 분(Minute) 캔들 | GET | `/v1/candles/minutes/{unit}` | `public/분minute-캔들.md` | candle, 캔들, 분봉, minute, OHLCV, 차트 |
| 일(Day) 캔들 | GET | `/v1/candles/days` | `public/일day-캔들.md` | candle, 캔들, 일봉, day, OHLCV, 차트 |
| 주(Week) 캔들 | GET | `/v1/candles/weeks` | `public/주week-캔들.md` | candle, 캔들, 주봉, week, OHLCV, 차트 |
| 월(Month) 캔들 | GET | `/v1/candles/months` | `public/월month-캔들.md` | candle, 캔들, 월봉, month, OHLCV, 차트 |

### 시세 체결 조회
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 최근 체결 내역 | GET | `/v1/trades/ticks` | `public/최근-체결-내역.md` | trade, 체결, 거래, tick, 최근 |

### 시세 현재가(Ticker) 조회
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 현재가 정보 | GET | `/v1/ticker` | `public/현재가-정보.md` | ticker, 현재가, 시세, price, 가격 |

### 시세 호가 정보(Orderbook) 조회
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 호가 정보 조회 | GET | `/v1/orderbook` | `public/호가-정보-조회.md` | orderbook, 호가, 매수, 매도, bid, ask |

### 서비스 정보
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 경보제 | GET | `/v1/market/virtual_asset_warning` | `public/경보제.md` | warning, 경보, 유의, 주의, 경고 |

---

## PRIVATE API (JWT 인증 필요)

### 자산
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 전체 계좌 조회 | GET | `/v1/accounts` | `private/전체-계좌-조회.md` | account, 계좌, 잔고, balance, 자산 |

### 주문
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 주문 가능 정보 | GET | `/v1/orders/chance` | `private/주문-가능-정보.md` | order, 주문, 가능, chance, 수수료 |
| 개별 주문 조회 | GET | `/v1/order` | `private/개별-주문-조회.md` | order, 주문, 조회, 상세, uuid |
| 주문 리스트 조회 | GET | `/v1/orders` | `private/주문-리스트-조회.md` | order, 주문, 리스트, 목록, list |
| 주문 취소 접수 | DELETE | `/v1/order` | `private/주문-취소-접수.md` | order, 주문, 취소, cancel, delete |
| 주문하기 | POST | `/v1/orders` | `private/주문하기.md` | order, 주문, 매수, 매도, buy, sell, bid, ask |

### TWAP
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| TWAP 주문 내역 조회 | GET | `/v1/twap/orders` | `private/twap-주문내역-조회.md` | twap, 주문, 내역, 조회 |
| TWAP 주문 취소 | DELETE | `/v1/twap/order` | `private/twap-주문-취소.md` | twap, 취소, cancel |
| TWAP 주문하기 | POST | `/v1/twap/orders` | `private/twap-주문-요청.md` | twap, 주문, 분할매수, 분할매도 |

### 출금
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 코인 출금 리스트 조회 | GET | `/v1/withdraws` | `private/출금-리스트-조회.md` | withdraw, 출금, 리스트, 코인 |
| 원화 출금 리스트 조회 | GET | `/v1/withdraws/krw` | `private/원화-출금-리스트-조회.md` | withdraw, 출금, 원화, KRW |
| 개별 출금 조회 | GET | `/v1/withdraw` | `private/개별-출금-조회.md` | withdraw, 출금, 개별, 상세 |
| 출금 가능 정보 | GET | `/v1/withdraws/chance` | `private/출금-가능-정보.md` | withdraw, 출금, 가능, 한도 |
| 가상자산 출금하기 | POST | `/v1/withdraws/coin` | `private/디지털-자산-출금하기.md` | withdraw, 출금, 코인, 전송, send |
| 원화 출금하기 | POST | `/v1/withdraws/krw` | `private/원화-출금하기.md` | withdraw, 출금, 원화, KRW |
| 출금 허용 주소 리스트 | GET | `/v1/withdraws/coin_addresses` | `private/출금-허용-주소-리스트-조회.md` | withdraw, 출금, 주소, 화이트리스트 |

### 입금
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 코인 입금 리스트 조회 | GET | `/v1/deposits` | `private/입금-리스트-조회.md` | deposit, 입금, 리스트, 코인 |
| 원화 입금 리스트 조회 | GET | `/v1/deposits/krw` | `private/원화-입금-리스트-조회.md` | deposit, 입금, 원화, KRW |
| 개별 입금 조회 | GET | `/v1/deposit` | `private/개별-입금-조회.md` | deposit, 입금, 개별, 상세 |
| 입금 주소 생성 요청 | POST | `/v1/deposits/generate_coin_address` | `private/입금-주소-생성-요청.md` | deposit, 입금, 주소, 생성, address |
| 전체 입금 주소 조회 | GET | `/v1/deposits/coin_addresses` | `private/전체-입금-주소-조회.md` | deposit, 입금, 주소, 전체 |
| 개별 입금 주소 조회 | GET | `/v1/deposits/coin_address` | `private/개별-입금-주소-조회.md` | deposit, 입금, 주소, 개별 |
| 원화 입금하기 | POST | `/v1/deposits/krw` | `private/원화-입금하기.md` | deposit, 입금, 원화, KRW |

### 서비스 정보
| 엔드포인트 | Method | Path | 파일 | 키워드 |
|-----------|--------|------|------|--------|
| 입출금 현황 | GET | `/v1/status/wallet` | `private/입출금-현황.md` | status, 현황, 지갑, wallet, 상태 |
| API 키 리스트 조회 | GET | `/v1/api_keys` | `private/api-키-리스트-조회.md` | api, key, 키, 인증, 권한 |

---

## 인증 방식

Private API는 JWT Bearer 토큰 인증 필요:

```
Authorization: Bearer {jwtToken}
```

JWT Payload:
- `access_key`: API 키
- `nonce`: UUID v4
- `timestamp`: 현재 시각 (밀리초)
- `query_hash`: 요청 파라미터의 SHA512 해시
- `query_hash_alg`: "SHA512"
