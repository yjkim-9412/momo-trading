# 해외주식 기간손익

**URL:** `/uapi/overseas-stock/v1/trading/inquire-period-profit`
**Method:** GET
**API ID:** v1_해외주식-032

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTS3039R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (공란: 전체, NASD: 미국, SEHK: 홍콩, SHAA: 중국, TKSE: 일본, HASE: 베트남) |
| NATN_CD | string | No | 국가코드 (공란 기본값) |
| CRCY_CD | string | Yes | 통화코드 (공란: 전체, USD/HKD/CNY/JPY/VND) |
| PDNO | string | No | 상품번호 (공란: 전체) |
| INQR_STRT_DT | string | Yes | 조회시작일자 (YYYYMMDD) |
| INQR_END_DT | string | Yes | 조회종료일자 (YYYYMMDD) |
| WCRC_FRCR_DVSN_CD | string | Yes | 원화외화구분코드 (01: 외화, 02: 원화) |
| CTX_AREA_FK200 | string | No | 연속조회검색조건200 |
| CTX_AREA_NK200 | string | No | 연속조회키200 |

## 응답

- `output1`: 종목별 기간손익 목록
- `output2`: 기간손익 요약

## 비고

- 특정 기간의 해외주식 매매 손익을 조회
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
