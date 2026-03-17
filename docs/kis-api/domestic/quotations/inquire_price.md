# 주식현재가 시세

**URL:** `/uapi/domestic-stock/v1/quotations/inquire-price`
**Method:** GET
**분류:** [국내주식] 기본시세 > 주식현재가 시세[v1_국내주식-008]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | FHKST01010100 | |
| 모의 | FHKST01010100 | 실전과 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT, UN:통합) |
| FID_INPUT_ISCD | string | Yes | 입력 종목코드 (ex. 005930, ETN은 종목코드 6자리 앞에 Q 입력 필수) |

## 응답

- **output** (object): 현재가 시세 데이터 (1건)

## 비고

- 실시간 시세를 원하면 웹소켓 API를 활용할 것
- 종목코드 마스터파일: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
