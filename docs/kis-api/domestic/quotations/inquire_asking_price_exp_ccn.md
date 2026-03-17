# 주식현재가 호가/예상체결

**URL:** `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`
**Method:** GET
**분류:** [국내주식] 기본시세 > 주식현재가 호가/예상체결[v1_국내주식-011]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | FHKST01010200 | |
| 모의 | FHKST01010200 | 실전과 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT, UN:통합) |
| FID_INPUT_ISCD | string | Yes | 입력 종목코드 (ex. 005930) |

## 응답

- **output1** (object): 호가 정보 (매수/매도 호가)
- **output2** (object): 예상체결 정보

## 비고

- 매수/매도 호가를 확인할 수 있음
- 실시간 데이터를 원하면 웹소켓 API를 활용할 것
