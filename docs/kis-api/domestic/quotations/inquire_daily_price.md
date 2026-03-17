# 주식현재가 일자별

**URL:** `/uapi/domestic-stock/v1/quotations/inquire-daily-price`
**Method:** GET
**분류:** [국내주식] 기본시세 > 주식현재가 일자별[v1_국내주식-010]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | FHKST01010400 | |
| 모의 | FHKST01010400 | 실전과 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT, UN:통합) |
| FID_INPUT_ISCD | string | Yes | 입력 종목코드 (ex. 005930) |
| FID_PERIOD_DIV_CODE | string | Yes | 기간 분류 코드 (D:일 최근 30거래일, W:주 최근 30주, M:월 최근 30개월) |
| FID_ORG_ADJ_PRC | string | Yes | 수정주가 원주가 가격 (0:수정주가미반영, 1:수정주가반영) |

## 응답

- **output** (array): 일자별 시세 데이터 목록

## 비고

- 일/주/월별 주가를 확인할 수 있으며, 최근 30일(주, 월)로 제한
- 수정주가는 액면분할/액면병합 등 권리 발생 시 과거 시세를 현재 주가에 맞게 보정한 가격
