# 해외주식 분봉조회

**URL:** `/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice`
**Method:** GET
**API ID:** v1_해외주식-030

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS76950200 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | "" 공백으로 입력 |
| EXCD | string | Yes | 거래소코드 (아래 참조) |
| SYMB | string | Yes | 종목코드 (예: TSLA) |
| NMIN | string | Yes | 분단위 (1: 1분봉, 2: 2분봉, ...) |
| PINC | string | Yes | 전일포함여부 (0: 당일, 1: 전일포함, 다음조회 시 반드시 "1") |
| NEXT | string | Yes | 처음조회 시 "" 공백, 다음조회 시 "1" |
| NREC | string | Yes | 레코드요청갯수 (최대 120) |
| FILL | string | No | 미체결채움구분 ("" 공백) |
| KEYB | string | Yes | NEXT KEY BUFF (처음 "" 공백, 다음조회 시 이전 마지막 데이터 기준 시간) |

## 거래소코드 (EXCD)

### 정규장
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

### 주간거래 (최대 1일치 분봉만 조회 가능)
| 코드 | 설명 |
|------|------|
| BAY | 뉴욕(주간) |
| BAQ | 나스닥(주간) |
| BAA | 아멕스(주간) |

## 응답

- `output1`: 종목 기본정보
- `output2`: 분봉 데이터 (배열)

## 비고

- 최대 120개 레코드 요청 가능
- 다음조회 시 `PINC`를 반드시 "1"로 설정
- 다음조회 시 `KEYB`에 이전 결과의 마지막 분봉 데이터로부터 1분 전 또는 n분 전의 시간 입력 (형식: YYYYMMDDHHMMSS, 예: 20241014140100)
- 주간거래 분봉은 최대 1일치만 조회 가능
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
