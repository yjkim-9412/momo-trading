# 해외주식 기간별시세

**URL:** `/uapi/overseas-price/v1/quotations/dailyprice`
**Method:** GET
**API ID:** v1_해외주식-010

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS76240000 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | 사용자권한정보 |
| EXCD | string | Yes | 거래소코드 (예: NAS) |
| SYMB | string | Yes | 종목코드 (예: TSLA) |
| GUBN | string | Yes | 일/주/월구분 (0: 일, 1: 주, 2: 월) |
| BYMD | string | No | 조회기준일자 (YYYYMMDD) |
| MODP | string | Yes | 수정주가반영여부 (0: 미반영, 1: 반영) |

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

- `output1`: 종목 기본정보
- `output2`: 기간별 시세 데이터 (배열)

## 비고

- 일/주/월 단위의 과거 시세를 조회
- 수정주가 반영 여부 선택 가능
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
- 실전/모의투자 공통 tr_id 사용
