# 거래량순위

**URL:** `/uapi/domestic-stock/v1/quotations/volume-rank`
**Method:** GET
**분류:** [국내주식] 순위분석 > 거래량순위[v1_국내주식-047]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 공통 | FHPST01710000 | 실전/모의 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| FID_COND_MRKT_DIV_CODE | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT, UN:통합, W:ELW) |
| FID_COND_SCR_DIV_CODE | string | Yes | 조건 화면 분류 코드 (20171 고정) |
| FID_INPUT_ISCD | string | Yes | 입력 종목코드 (0000:전체, 기타:업종코드) |
| FID_DIV_CLS_CODE | string | Yes | 분류 구분 코드 (0:전체, 1:보통주, 2:우선주) |
| FID_BLNG_CLS_CODE | string | Yes | 소속 구분 코드 (0:평균거래량, 1:거래증가율, 2:평균거래회전율, 3:거래금액순, 4:평균거래금액회전율) |
| FID_TRGT_CLS_CODE | string | Yes | 대상 구분 코드 (9자리, 각 자리 "1" 또는 "0": 증거금30%/40%/50%/60%/100%/신용보증금30%/40%/50%/60%) |
| FID_TRGT_EXLS_CLS_CODE | string | Yes | 대상 제외 구분 코드 (10자리, 각 자리 "1" 또는 "0": 투자위험경고주의/관리종목/정리매매/불성실공시/우선주/거래정지/ETF/ETN/신용주문불가/SPAC) |
| FID_INPUT_PRICE_1 | string | Yes | 입력 가격1 (가격 하한, 전체 조회 시 공란) |
| FID_INPUT_PRICE_2 | string | Yes | 입력 가격2 (가격 상한, 전체 조회 시 공란) |
| FID_VOL_CNT | string | Yes | 거래량 수 (최소 거래량, 전체 조회 시 공란) |
| FID_INPUT_DATE_1 | string | Yes | 입력 날짜1 (공란) |

## 응답

- **output** (array): 거래량 순위 목록

## 페이징

- 연속조회: `tr_cont`가 "M"이면 다음 페이지 존재

## 비고

- 전체 종목 조회 시 `FID_INPUT_ISCD`에 "0000" 입력
