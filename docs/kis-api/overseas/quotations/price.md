# 해외주식 현재체결가

**URL:** `/uapi/overseas-price/v1/quotations/price`
**Method:** GET
**API ID:** v1_해외주식-009

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS00000300 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | 사용자권한정보 |
| EXCD | string | Yes | 거래소코드 (예: NAS, NYS, AMS 등) |
| SYMB | string | Yes | 종목코드 (예: AAPL) |

## 거래소코드 (EXCD)

| 코드 | 설명 |
|------|------|
| NAS | 나스닥 |
| NYS | 뉴욕 |
| AMS | 아멕스 |
| HKS | 홍콩 |
| SHS | 상해 |
| SZS | 심천 |
| TSE | 도쿄 |
| HSX | 호치민 |
| HNX | 하노이 |

## 비고

- 시세 API에서 거래소코드는 주문 API와 다름 (NAS vs NASD, NYS vs NYSE 등)
- 실전/모의투자 공통 tr_id 사용
- 연속조회 지원
