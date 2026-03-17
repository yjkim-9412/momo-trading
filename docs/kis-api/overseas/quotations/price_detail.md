# 해외주식 현재가상세

**URL:** `/uapi/overseas-price/v1/quotations/price-detail`
**Method:** GET
**API ID:** v1_해외주식-029

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS76200200 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | 사용자권한정보 |
| EXCD | string | Yes | 거래소명 (아래 참조) |
| SYMB | string | Yes | 종목코드 |

## 거래소코드 (EXCD)

| 코드 | 설명 |
|------|------|
| NAS | 나스닥 |
| NYS | 뉴욕 |
| AMS | 아멕스 |
| HKS | 홍콩 |
| SHS | 상해 |
| SZS | 심천 |
| SHI | 상해지수 |
| SZI | 심천지수 |
| TSE | 도쿄 |
| HSX | 호치민 |
| HNX | 하노이 |
| BAY | 뉴욕(주간) |
| BAQ | 나스닥(주간) |
| BAA | 아멕스(주간) |

## 비고

- 현재체결가(`price`)보다 더 상세한 종목 정보를 반환
- 주간거래 시세도 조회 가능 (BAY, BAQ, BAA)
- 실전/모의투자 공통 tr_id 사용
