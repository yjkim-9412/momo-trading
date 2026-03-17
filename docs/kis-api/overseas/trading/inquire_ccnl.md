# 해외주식 주문체결내역

**URL:** `/uapi/overseas-stock/v1/trading/inquire-ccnl`
**Method:** GET
**API ID:** v1_해외주식-007

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTS3035R | |
| 모의 | VTTS3035R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| PDNO | string | Yes | 상품번호 (전종목: "%" 입력, 모의투자: ""만 가능) |
| ORD_STRT_DT | string | Yes | 주문시작일자 (YYYYMMDD, 현지시각 기준) |
| ORD_END_DT | string | Yes | 주문종료일자 (YYYYMMDD, 현지시각 기준) |
| SLL_BUY_DVSN | string | Yes | 매도매수구분 (00: 전체, 01: 매도, 02: 매수, 모의투자: "00"만 가능) |
| CCLD_NCCS_DVSN | string | Yes | 체결미체결구분 (00: 전체, 01: 체결, 02: 미체결, 모의투자: "00"만 가능) |
| OVRS_EXCG_CD | string | No | 해외거래소코드 (전종목: "%" 입력, 모의투자: ""만 가능) |
| SORT_SQN | string | Yes | 정렬순서 (DS: 정순, AS: 역순, 모의투자: DS 고정) |
| ORD_DT | string | No | 주문일자 ("" Null 설정) |
| ORD_GNO_BRNO | string | No | 주문채번지점번호 ("" Null 설정) |
| ODNO | string | No | 주문번호 (반드시 "" Null 설정, 주문번호 검색 불가) |
| CTX_AREA_NK200 | string | No | 연속조회키200 (최초 조회 시 공란) |
| CTX_AREA_FK200 | string | No | 연속조회검색조건200 (최초 조회 시 공란) |

## 거래소코드 (OVRS_EXCG_CD)

| 코드 | 설명 |
|------|------|
| NASD | 미국시장 전체 (나스닥, 뉴욕, 아멕스) |
| NYSE | 뉴욕 |
| AMEX | 아멕스 |
| SEHK | 홍콩 |
| SHAA | 중국상해 |
| SZAA | 중국심천 |
| TKSE | 일본 |
| HASE | 베트남 하노이 |
| VNSE | 베트남 호치민 |

## 비고

- 모의투자 계좌에서는 제한 사항이 많음: `PDNO`는 ""만, `SLL_BUY_DVSN`/`CCLD_NCCS_DVSN`은 "00"만, `OVRS_EXCG_CD`는 ""만 가능
- `ODNO`(주문번호)로 검색 불가 -- 반드시 "" 설정
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
- 일자는 현지시각 기준
