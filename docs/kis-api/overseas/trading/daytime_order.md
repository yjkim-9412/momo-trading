# 해외주식 미국주간주문

**URL:** `/uapi/overseas-stock/v1/trading/daytime-order`
**Method:** POST
**API ID:** v1_해외주식-026

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 미국 매수 | TTTS6036U | 실전 전용 |
| 미국 매도 | TTTS6037U | 실전 전용 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX) |
| PDNO | string | Yes | 종목코드 |
| ORD_QTY | string | Yes | 주문수량 (해외거래소별 최소 주문수량 및 주문단위 확인 필요) |
| OVRS_ORD_UNPR | string | Yes | 해외주문단가 (소수점 포함, 시장가 "0" 입력) |
| CTAC_TLNO | string | No | 연락전화번호 |
| MGCO_APTM_ODNO | string | No | 운용사지정주문번호 |
| ORD_SVR_DVSN_CD | string | Yes | 주문서버구분코드 ("0") |
| ORD_DVSN | string | Yes | 주문구분 (00: 지정가, 주간거래는 지정가만 가능) |

## 비고

- 미국 주간거래 전용 주문 API (미국 거래소만 지원: NASD, NYSE, AMEX)
- 주간거래는 **지정가(00)만** 가능
- `order_dv` 파라미터로 매수(`buy`)/매도(`sell`)를 구분하며, 이에 따라 tr_id가 자동 선택됨
- 모의투자 미지원
