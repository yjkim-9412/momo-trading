# 해외주식 미체결내역

**URL:** `/uapi/overseas-stock/v1/trading/inquire-nccs`
**Method:** GET
**API ID:** v1_해외주식-005

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전/모의 | TTTS3018R | 공통 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (아래 참조) |
| SORT_SQN | string | Yes | 정렬순서 (DS: 정순, 그외: 역순, TTTS3018R인 경우 ""공란) |
| CTX_AREA_FK200 | string | No | 연속조회검색조건200 (최초 조회 시 공란) |
| CTX_AREA_NK200 | string | No | 연속조회키200 (최초 조회 시 공란) |

## 거래소코드 (OVRS_EXCG_CD)

| 코드 | 설명 |
|------|------|
| NASD | 나스닥 (미국전체 조회) |
| NYSE | 뉴욕 |
| AMEX | 아멕스 |
| SEHK | 홍콩 |
| SHAA | 중국상해 |
| SZAA | 중국심천 |
| TKSE | 일본 |
| HASE | 베트남 하노이 |
| VNSE | 베트남 호치민 |

## 비고

- `NASD`인 경우에만 미국전체로 조회되며, 나머지 거래소 코드는 해당 거래소만 조회됨
- 공백 입력 시 다음조회가 불가능하므로, 반드시 거래소코드를 입력해야 함
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
