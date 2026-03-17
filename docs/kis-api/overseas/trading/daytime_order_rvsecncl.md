# 해외주식 미국주간정정취소

**URL:** `/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl`
**Method:** POST
**API ID:** v1_해외주식-027

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTS6038U | 모의투자 미지원 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX) |
| PDNO | string | Yes | 종목코드 |
| ORGN_ODNO | string | Yes | 원주문번호 (정정 또는 취소할 주문번호) |
| RVSE_CNCL_DVSN_CD | string | Yes | 정정취소구분코드 (01: 정정, 02: 취소) |
| ORD_QTY | string | Yes | 주문수량 |
| OVRS_ORD_UNPR | string | Yes | 해외주문단가 (소수점 포함) |
| CTAC_TLNO | string | No | 연락전화번호 |
| MGCO_APTM_ODNO | string | No | 운용사지정주문번호 |
| ORD_SVR_DVSN_CD | string | Yes | 주문서버구분코드 |

## 비고

- 미국 주간거래 정정/취소 전용 API
- 미국 거래소만 지원: NASD, NYSE, AMEX
- 모의투자 미지원
