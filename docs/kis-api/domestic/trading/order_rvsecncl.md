# 주식주문(정정취소)

**URL:** `/uapi/domestic-stock/v1/trading/order-rvsecncl`
**Method:** POST
**분류:** [국내주식] 주문/계좌 > 주식주문(정정취소)[v1_국내주식-003]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTC0013U | |
| 모의 | VTTC0013U | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 |
| KRX_FWDG_ORD_ORGNO | string | Yes | 한국거래소전송주문조직번호 |
| ORGN_ODNO | string | Yes | 원주문번호 |
| ORD_DVSN | string | Yes | 주문구분 |
| RVSE_CNCL_DVSN_CD | string | Yes | 정정취소구분코드 (01:정정, 02:취소) |
| ORD_QTY | string | Yes | 주문수량 |
| ORD_UNPR | string | Yes | 주문단가 |
| QTY_ALL_ORD_YN | string | Yes | 잔량전부주문여부 (Y:전량, N:일부) |
| EXCG_ID_DVSN_CD | string | Yes | 거래소ID구분코드 (KRX:한국거래소, NXT:대체거래소, SOR:SOR) |
| CNDT_PRIC | string | No | 조건가격 |

## 응답

- **output** (object): 정정/취소 결과 데이터

## 비고

- 이미 체결된 건은 정정 및 취소 불가
- 정정은 원주문에 대한 주문단가 혹은 주문구분을 변경하는 것으로, 정정 가능 수량은 원주문수량을 초과할 수 없음
- 호출 전 반드시 `주식정정취소가능주문조회`(`inquire_psbl_rvsecncl`)를 통해 정정취소가능수량(`output > psbl_qty`)을 확인한 후 정정/취소 주문 수행 권장
- POST API의 BODY key값은 대문자로 작성 필수
