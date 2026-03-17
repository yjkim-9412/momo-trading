# 해외주식 매수가능금액조회

**URL:** `/uapi/overseas-stock/v1/trading/inquire-psamount`
**Method:** GET
**API ID:** v1_해외주식-014

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTS3007R | |
| 모의 | VTTS3007R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX/SEHK/SHAA/SZAA/TKSE/HASE/VNSE) |
| OVRS_ORD_UNPR | string | Yes | 해외주문단가 (정수부 23자리, 소수부 8자리, 예: "23.8") |
| ITEM_CD | string | Yes | 종목코드 (예: QQQ) |

## 비고

- 특정 종목을 특정 가격으로 매수할 때의 매수가능금액/수량을 조회
- 해외주문단가는 소수점 포함하여 입력 (예: "1.4", "150.00")
- 연속조회 지원: `tr_cont`가 "M"이면 다음 페이지 존재
