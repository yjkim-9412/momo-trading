# 주식일별주문체결조회

**URL:** `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`
**Method:** GET
**분류:** [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005]

## tr_id

| 구분 | pd_dv | tr_id | 비고 |
|------|-------|-------|------|
| 실전 | inner (3개월이내) | TTTC0081R | |
| 실전 | before (3개월이전) | CTSC9215R | |
| 모의 | inner (3개월이내) | VTTC0081R | |
| 모의 | before (3개월이전) | VTSC9215R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (2자리) |
| INQR_STRT_DT | string | Yes | 조회시작일자 (YYYYMMDD) |
| INQR_END_DT | string | Yes | 조회종료일자 (YYYYMMDD) |
| SLL_BUY_DVSN_CD | string | Yes | 매도매수구분코드 (00:전체 / 01:매도 / 02:매수) |
| PDNO | string | No | 상품번호 (종목코드) |
| CCLD_DVSN | string | Yes | 체결구분 (00:전체 / 01:체결 / 02:미체결) |
| INQR_DVSN | string | Yes | 조회구분 (00:역순 / 01:정순) |
| INQR_DVSN_3 | string | Yes | 조회구분3 (00:전체 / 01:현금 / 02:신용 / 03:담보 / 04:대주 / 05:대여 / 06:자기융자신규/상환 / 07:유통융자신규/상환) |
| ORD_GNO_BRNO | string | No | 주문채번지점번호 |
| ODNO | string | No | 주문번호 (한국투자증권 시스템에서 채번된 주문번호) |
| INQR_DVSN_1 | string | No | 조회구분1 (공란:전체 / 1:ELW / 2:프리보드) |
| CTX_AREA_FK100 | string | No | 연속조회검색조건100 (공란:최초조회 / 이전 조회 Output 사용) |
| CTX_AREA_NK100 | string | No | 연속조회키100 (공란:최초조회 / 이전 조회 Output 사용) |
| EXCG_ID_DVSN_CD | string | No | 거래소ID구분코드 (KRX / NXT / SOR / ALL), 기본값: KRX |

## 응답

- **output1** (array): 주문체결 상세 내역 목록
- **output2** (object): 요약 정보

## 페이징

- 실전: 한 번 호출에 최대 100건, 이후 연속조회
- 모의: 한 번 호출에 최대 15건, 이후 연속조회
- 연속조회: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재, `CTX_AREA_FK100`/`CTX_AREA_NK100` 값을 다음 요청에 전달

## 비고

- 3개월이전 체결내역 조회(`CTSC9215R`)의 경우, 장중에는 DB 부하로 인해 응답 지연이 있을 수 있음
  - 가급적 장 종료 이후(15:30 이후) 조회 권장
  - 조회기간(`INQR_STRT_DT`~`INQR_END_DT`)을 짧게 설정하여 조회 권장
