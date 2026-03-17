# 해외주식 종목/지수/환율 기간별시세 (일/주/월/년)

**URL:** `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`
**Method:** GET
**API ID:** v1_해외주식-012

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | FHKST03030100 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 시장분류코드 (N: 해외지수, X: 환율, I: 국채, S: 금선물) |
| FID_INPUT_ISCD | string | Yes | 종목코드 (해외주식 마스터 코드 참조, 예: .DJI) |
| FID_INPUT_DATE_1 | string | Yes | 시작일자 (YYYYMMDD) |
| FID_INPUT_DATE_2 | string | Yes | 종료일자 (YYYYMMDD) |
| FID_PERIOD_DIV_CODE | string | Yes | 기간구분코드 (D: 일, W: 주, M: 월, Y: 년) |

## 응답

- `output1`: 종목/지수 기본정보
- `output2`: 기간별 시세 데이터 (배열)

## 비고

- 해외 **지수**, **환율**, **국채**, **금선물** 기간별 시세 조회 API
- 미국주식 조회 시 다우30, 나스닥100, S&P500 종목만 조회 가능
- 더 많은 미국주식 종목 시세는 `dailyprice` API 사용 권장
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
