# 해외주식 잔고

**URL:** `/uapi/overseas-stock/v1/trading/inquire-balance`
**Method:** GET
**API ID:** v1_해외주식-006

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 실전 | TTTS3012R | |
| 모의 | VTTS3012R | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (아래 참조) |
| TR_CRCY_CD | string | Yes | 거래통화코드 (USD/HKD/CNY/JPY/VND) |
| CTX_AREA_FK200 | string | No | 연속조회검색조건200 (최초 조회 시 공란, 다음 페이지 시 이전 output값) |
| CTX_AREA_NK200 | string | No | 연속조회키200 (최초 조회 시 공란, 다음 페이지 시 이전 output값) |

## 거래소코드 (OVRS_EXCG_CD)

### 실전투자
| 코드 | 설명 |
|------|------|
| NASD | 미국전체 |
| NAS | 나스닥 |
| NYSE | 뉴욕 |
| AMEX | 아멕스 |
| SEHK | 홍콩 |
| SHAA | 중국상해 |
| SZAA | 중국심천 |
| TKSE | 일본 |
| HASE | 베트남 하노이 |
| VNSE | 베트남 호치민 |

### 모의투자
| 코드 | 설명 |
|------|------|
| NASD | 나스닥 |
| NYSE | 뉴욕 |
| AMEX | 아멕스 |
| SEHK | 홍콩 |
| SHAA | 중국상해 |
| SZAA | 중국심천 |
| TKSE | 일본 |
| HASE | 베트남 하노이 |
| VNSE | 베트남 호치민 |

## 응답

- `output1`: 보유종목 목록 (배열)
- `output2`: 계좌 요약 정보

## 비고

- 실전투자에서 `NASD`는 미국전체(나스닥+뉴욕+아멕스) 조회, 모의투자에서는 나스닥만 조회
- 연속조회 지원: `tr_cont`가 "M" 또는 "F"이면 다음 페이지 존재
- 페이징 시 `CTX_AREA_FK200`, `CTX_AREA_NK200` 값을 이전 응답에서 가져와 설정
