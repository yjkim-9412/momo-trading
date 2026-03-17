# 해외지수 분봉조회

**URL:** `/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice`
**Method:** GET
**API ID:** v1_해외주식-031

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | FHKST03030200 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 시장분류코드 (N: 해외지수, X: 환율, KX: 원화환율) |
| FID_INPUT_ISCD | string | Yes | 종목번호 (예: SPX, .DJI) |
| FID_HOUR_CLS_CODE | string | Yes | 시간구분코드 (0: 정규장, 1: 시간외) |
| FID_PW_DATA_INCU_YN | string | Yes | 과거데이터 포함여부 (Y/N) |

## 응답

- `output1`: 지수 기본정보
- `output2`: 분봉 데이터 (배열)

## 비고

- 해외 **지수** 및 **환율**의 분봉 데이터 조회
- 정규장/시간외 구분 가능
- 과거 데이터 포함 여부 선택 가능
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
