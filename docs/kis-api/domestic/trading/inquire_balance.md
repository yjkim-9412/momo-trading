# 주식잔고조회

**URL:** `/uapi/domestic-stock/v1/trading/inquire-balance`
**Method:** GET
**분류:** [국내주식] 주문/계좌 > 주식잔고조회[v1_국내주식-006]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTC8434R | |
| 모의 | VTTC8434R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| AFHR_FLPR_YN | string | Yes | 시간외단일가/거래소여부 (N:기본값, Y:시간외단일가, X:NXT) |
| OFL_YN | string | No | 오프라인여부 (공란 고정) |
| INQR_DVSN | string | Yes | 조회구분 (01:대출일별 / 02:종목별) |
| UNPR_DVSN | string | Yes | 단가구분 (01) |
| FUND_STTL_ICLD_YN | string | Yes | 펀드결제분포함여부 (N / Y) |
| FNCG_AMT_AUTO_RDPT_YN | string | Yes | 융자금액자동상환여부 (N) |
| PRCS_DVSN | string | Yes | 처리구분 (00:전일매매포함 / 01:전일매매미포함) |
| CTX_AREA_FK100 | string | No | 연속조회검색조건100 |
| CTX_AREA_NK100 | string | No | 연속조회키100 |

## 응답

- **output1** (array): 보유종목 상세 목록
- **output2** (object): 계좌 요약 정보

## 페이징

- 실전: 한 번 호출에 최대 50건, 이후 연속조회
- 모의: 한 번 호출에 최대 20건, 이후 연속조회
- 연속조회: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재

## 비고

- 당일 전량매도한 잔고도 보유수량 0으로 보여질 수 있으나, 해당 보유수량 0인 잔고는 최종 D-2일 이후에는 잔고에서 사라짐
