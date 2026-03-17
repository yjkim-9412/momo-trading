# 매수가능조회

**URL:** `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
**Method:** GET
**분류:** [국내주식] 주문/계좌 > 매수가능조회[v1_국내주식-007]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTC8908R | |
| 모의 | VTTC8908R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| PDNO | string | Yes | 상품번호 (종목번호 6자리) |
| ORD_UNPR | string | Yes | 주문단가 (1주당 가격) |
| ORD_DVSN | string | Yes | 주문구분 (01:시장가 등) |
| CMA_EVLU_AMT_ICLD_YN | string | Yes | CMA평가금액포함여부 (Y / N) |
| OVRS_ICLD_YN | string | Yes | 해외포함여부 (N) |

## 응답

- **output** (object): 매수가능 정보 (1건)

## 비고

- 실전/모의 모두 한 번 호출에 최대 1건 확인 가능

### 매수가능금액 확인
- 미수 사용 X: `nrcvb_buy_amt` (미수없는매수금액) 확인
- 미수 사용 O: `max_buy_amt` (최대매수금액) 확인

### 매수가능수량 확인
- 특정 종목 전량매수 시 가능수량을 확인할 경우 `ORD_DVSN:00`(지정가)은 종목증거금율이 반영되지 않음
- **반드시** `ORD_DVSN:01`(시장가)로 지정하여 종목증거금율이 반영된 가능수량을 확인할 것
  - 조건부지정가 등 특정 주문구분(ex. IOC)으로 주문 시에는 동일한 주문구분 입력
- 미수 사용 X: `nrcvb_buy_qty` (미수없는매수수량) 확인
- 미수 사용 O: `max_buy_qty` (최대매수수량) 확인
