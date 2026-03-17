# 해외결제일자조회 (각국 휴장일)

**URL:** `/uapi/overseas-stock/v1/quotations/countries-holiday`
**Method:** GET
**API ID:** 해외주식-017

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | CTOS5011R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| TRAD_DT | string | Yes | 기준일자 (YYYYMMDD) |
| CTX_AREA_NK | string | No | 연속조회키 (최초 조회 시 공백) |
| CTX_AREA_FK | string | No | 연속조회검색조건 (최초 조회 시 공백) |

## 비고

- 기준일자 기준으로 각국 해외 거래소의 휴장일/결제일 정보를 조회
- 연속조회 지원: `tr_cont`가 "M"이면 다음 페이지 존재
- URL 경로가 다른 시세 API와 다름 (`/uapi/overseas-stock/v1/quotations/`)
