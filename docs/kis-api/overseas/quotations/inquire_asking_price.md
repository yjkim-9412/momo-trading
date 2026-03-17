# 해외주식 현재가 1호가

**URL:** `/uapi/overseas-price/v1/quotations/inquire-asking-price`
**Method:** GET
**API ID:** 해외주식-033

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS76200100 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | 사용자권한정보 |
| EXCD | string | Yes | 거래소코드 (NYS/NAS/AMS 등) |
| SYMB | string | Yes | 종목코드 (예: TSLA) |

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

## 응답

- `output1`: 호가 정보
- `output2`: 매수/매도 호가 상세
- `output3`: 추가 정보

## 비고

- 해외주식의 현재 최우선 호가(1호가) 조회
- 실전/모의투자 공통 tr_id 사용
