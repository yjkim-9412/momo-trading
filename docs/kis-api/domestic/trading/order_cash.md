# 주식주문(현금)

**URL:** `/uapi/domestic-stock/v1/trading/order-cash`
**Method:** POST
**분류:** [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001]

## tr_id

| 구분 | 매수/매도 | tr_id | 비고 |
|------|-----------|-------|------|
| 실전 | 매도(sell) | TTTC0011U | |
| 실전 | 매수(buy) | TTTC0012U | |
| 모의 | 매도(sell) | VTTC0011U | |
| 모의 | 매수(buy) | VTTC0012U | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 |
| PDNO | string | Yes | 상품번호 (종목코드 6자리, ETN은 7자리) |
| ORD_DVSN | string | Yes | 주문구분 (00:지정가 등) |
| ORD_QTY | string | Yes | 주문수량 |
| ORD_UNPR | string | Yes | 주문단가 |
| EXCG_ID_DVSN_CD | string | Yes | 거래소ID구분코드 (KRX) |
| SLL_TYPE | string | No | 매도유형 (매도주문 시: 01:일반매도, 02:임의매매, 05:대차매도) |
| CNDT_PRIC | string | No | 조건가격 (스탑지정가호가 주문 시 사용) |

## 응답

- **output** (object): 주문 결과 데이터

## 비고

- `TTC0802U`(현금매수) 사용 시 미수매수 가능 (증거금40% 계좌 신청 필요)
- 신용매수는 별도 API(`order_credit`) 사용
- `ORD_QTY`, `ORD_UNPR` 등을 String으로 전달해야 함
- `ORD_UNPR`(주문단가)가 없는 주문은 상한가로 주문금액을 선정하고, 체결 시 체결금액으로 정산
- POST API의 BODY key값은 대문자로 작성 필수
- 종목코드 마스터파일: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
