# 해외주식 결제기준잔고

**URL:** `/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance`
**Method:** GET
**API ID:** 해외주식-064

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | CTRP6010R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 |
| BASS_DT | string | Yes | 기준일자 (YYYYMMDD) |
| WCRC_FRCR_DVSN_CD | string | Yes | 원화외화구분코드 (01: 원화기준, 02: 외화기준) |
| INQR_DVSN_CD | string | Yes | 조회구분코드 (00: 전체, 01: 일반, 02: 미니스탁) |

## 응답

- `output1`: 보유종목 목록
- `output2`: 계좌 요약
- `output3`: 추가 정보

## 비고

- 결제 기준의 잔고를 조회하는 API (체결기준 잔고와 구별)
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
