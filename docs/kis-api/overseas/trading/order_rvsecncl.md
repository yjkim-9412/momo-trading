# 해외주식 정정취소주문

**URL:** `/uapi/overseas-stock/v1/trading/order-rvsecncl`
**Method:** POST
**API ID:** v1_해외주식-003

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTT1004U | |
| 모의 | VTTT1004U | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX/SEHK/SHAA/SZAA/TKSE/HASE/VNSE) |
| PDNO | string | Yes | 상품번호 (종목코드) |
| ORGN_ODNO | string | Yes | 원주문번호 (해외주식_주문 API output ODNO 또는 미체결내역 API output ODNO) |
| RVSE_CNCL_DVSN_CD | string | Yes | 정정취소구분코드 (01: 정정, 02: 취소) |
| ORD_QTY | string | Yes | 주문수량 |
| OVRS_ORD_UNPR | string | Yes | 해외주문단가 (취소주문 시 "0" 입력) |
| MGCO_APTM_ODNO | string | No | 운용사지정주문번호 |
| ORD_SVR_DVSN_CD | string | Yes | 주문서버구분코드 ("0" 기본값) |

## 비고

- 정정취소구분코드: `01`(정정), `02`(취소)
- 취소주문 시 `OVRS_ORD_UNPR`에 "0" 입력
- 원주문번호(`ORGN_ODNO`)는 해외주식 주문 API의 output ODNO 또는 미체결내역 API의 output ODNO를 참조
- 모의투자 시 tr_id가 `VTTT1004U`로 변경
