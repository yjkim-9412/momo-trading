# 주식당일분봉조회

**URL:** `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice`
**Method:** GET
**분류:** [국내주식] 기본시세 > 주식당일분봉조회[v1_국내주식-022]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | FHKST03010200 | |
| 모의 | FHKST03010200 | 실전과 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT, UN:통합) |
| FID_INPUT_ISCD | string | Yes | 입력 종목코드 (ex. 005930) |
| FID_INPUT_HOUR_1 | string | Yes | 입력 시간1 (ex. 093000) |
| FID_PW_DATA_INCU_YN | string | Yes | 과거 데이터 포함 여부 (Y / N) |
| FID_ETC_CLS_CODE | string | No | 기타 구분 코드 |

## 응답

- **output1** (object): 현재가 요약 정보 (1건)
- **output2** (array): 분봉 데이터 목록

## 비고

- 한 번 호출에 최대 30건까지 확인 가능 (실전/모의 동일)
- **당일 분봉 데이터만 제공됨** (전일자 분봉 미제공)
- `FID_INPUT_HOUR_1`에 미래 시간 입력 시 현재가로 조회됨
  - 예: 오전 10시에 113000 입력 시 오전 10시~11시30분 사이의 데이터가 오전 10시 값으로 조회
- output2의 첫 번째 배열의 체결량(`cntg_vol`)은 첫 체결이 발생되기 전까지는 이전 분봉의 체결량이 표시됨. 해당 분봉의 첫 체결 발생 시 이전 분 체결량이 두 번째 배열로 이동하면서 새로운 체결량으로 업데이트
