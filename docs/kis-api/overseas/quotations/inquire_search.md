# 해외주식 조건검색

**URL:** `/uapi/overseas-price/v1/quotations/inquire-search`
**Method:** GET
**API ID:** v1_해외주식-015

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 공통 | HHDFS76410000 | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| AUTH | string | No | "" (Null 값 설정) |
| EXCD | string | Yes | 거래소코드 (NYS/NAS/AMS/HKS/SHS/SZS/HSX/HNX/TSE) |
| CO_YN_PRICECUR | string | No | 현재가선택조건 (1: 사용, 미사용 시 생략) |
| CO_ST_PRICECUR | string | No | 현재가시작범위가 (각국 통화: JPY/USD/HKD/CNY/VND) |
| CO_EN_PRICECUR | string | No | 현재가끝범위가 |
| CO_YN_RATE | string | No | 등락율선택조건 (1: 사용) |
| CO_ST_RATE | string | No | 등락율시작율 (%) |
| CO_EN_RATE | string | No | 등락율끝율 (%) |
| CO_YN_VALX | string | No | 시가총액선택조건 (1: 사용) |
| CO_ST_VALX | string | No | 시가총액시작액 (단위: 천) |
| CO_EN_VALX | string | No | 시가총액끝액 (단위: 천) |
| CO_YN_SHAR | string | No | 발행주식수선택조건 (1: 사용) |
| CO_ST_SHAR | string | No | 발행주식시작수 (단위: 천) |
| CO_EN_SHAR | string | No | 발행주식끝수 (단위: 천) |
| CO_YN_VOLUME | string | No | 거래량선택조건 (1: 사용) |
| CO_ST_VOLUME | string | No | 거래량시작량 (단위: 주) |
| CO_EN_VOLUME | string | No | 거래량끝량 (단위: 주) |
| CO_YN_AMT | string | No | 거래대금선택조건 (1: 사용) |
| CO_ST_AMT | string | No | 거래대금시작금 (단위: 천) |
| CO_EN_AMT | string | No | 거래대금끝금 (단위: 천) |
| CO_YN_EPS | string | No | EPS선택조건 (1: 사용) |
| CO_ST_EPS | string | No | EPS시작 |
| CO_EN_EPS | string | No | EPS끝 |
| CO_YN_PER | string | No | PER선택조건 (1: 사용) |
| CO_ST_PER | string | No | PER시작 |
| CO_EN_PER | string | No | PER끝 |
| KEYB | string | No | NEXT KEY BUFF ("" 공백 입력) |

## 응답

- `output1`: 검색 결과 목록
- `output2`: 요약 정보

## 비고

- 현재가, 등락율, 시가총액, 발행주식수, 거래량, 거래대금, EPS, PER 등 다양한 조건으로 해외주식 검색
- 조건을 사용하려면 해당 `CO_YN_*` 값을 "1"로 설정
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
