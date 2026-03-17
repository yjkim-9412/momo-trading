# 해외주식 주문

**URL:** `/uapi/overseas-stock/v1/trading/order`
**Method:** POST
**API ID:** v1_해외주식-001

## tr_id

### 매수 주문

| 거래소 | 실전 tr_id | 모의 tr_id | 비고 |
|--------|-----------|-----------|------|
| 미국 (NASD/NYSE/AMEX) | TTTT1002U | VTTT1002U | 미국 매수 주문 |
| 홍콩 (SEHK) | TTTS1002U | VTTS1002U | 홍콩 매수 주문 |
| 중국상해 (SHAA) | TTTS0202U | VTTS0202U | 중국상해 매수 주문 |
| 중국심천 (SZAA) | TTTS0305U | VTTS0305U | 중국심천 매수 주문 |
| 일본 (TKSE) | TTTS0308U | VTTS0308U | 일본 매수 주문 |
| 베트남 (HASE/VNSE) | TTTS0311U | VTTS0311U | 베트남 매수 주문 |

### 매도 주문

| 거래소 | 실전 tr_id | 모의 tr_id | 비고 |
|--------|-----------|-----------|------|
| 미국 (NASD/NYSE/AMEX) | TTTT1006U | VTTT1006U | 미국 매도 주문 |
| 홍콩 (SEHK) | TTTS1001U | VTTS1001U | 홍콩 매도 주문 |
| 중국상해 (SHAA) | TTTS1005U | VTTS1005U | 중국상해 매도 주문 |
| 중국심천 (SZAA) | TTTS0304U | VTTS0304U | 중국심천 매도 주문 |
| 일본 (TKSE) | TTTS0307U | VTTS0307U | 일본 매도 주문 |
| 베트남 (HASE/VNSE) | TTTS0310U | VTTS0310U | 베트남 매도 주문 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 (계좌번호 체계 8-2의 앞 8자리) |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 (계좌번호 체계 8-2의 뒤 2자리) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX/SEHK/SHAA/SZAA/TKSE/HASE/VNSE) |
| PDNO | string | Yes | 종목코드 (예: AAPL) |
| ORD_QTY | string | Yes | 주문수량 (해외거래소별 최소 주문수량 및 주문단위 확인 필요) |
| OVRS_ORD_UNPR | string | Yes | 해외주문단가 (1주당 가격, 시장가의 경우 "0" 입력) |
| CTAC_TLNO | string | No | 연락전화번호 |
| MGCO_APTM_ODNO | string | No | 운용사지정주문번호 |
| SLL_TYPE | string | Auto | 매도시 "00", 매수시 "" (자동 설정) |
| ORD_SVR_DVSN_CD | string | Yes | 주문서버구분코드 ("0" 기본값) |
| ORD_DVSN | string | Yes | 주문구분 (아래 참조) |

## 주문구분 (ORD_DVSN) 상세

### 미국 매수 (TTTT1002U)
| 코드 | 설명 | 모의투자 |
|------|------|---------|
| 00 | 지정가 | 가능 |
| 32 | LOO (장개시지정가) | 불가 |
| 34 | LOC (장마감지정가) | 불가 |

### 미국 매도 (TTTT1006U)
| 코드 | 설명 | 모의투자 |
|------|------|---------|
| 00 | 지정가 | 가능 |
| 31 | MOO (장개시시장가) | 불가 |
| 32 | LOO (장개시지정가) | 불가 |
| 33 | MOC (장마감시장가) | 불가 |
| 34 | LOC (장마감지정가) | 불가 |

### 홍콩 매도 (TTTS1001U)
| 코드 | 설명 | 모의투자 |
|------|------|---------|
| 00 | 지정가 | 가능 |
| 50 | 단주지정가 | 불가 |

### 기타 거래소
주문구분 코드 미사용 (제거)

## 비고

- `ord_dv` 파라미터로 매수(`buy`)/매도(`sell`)를 구분하며, 이에 따라 tr_id가 자동 선택됨
- 모의투자 시 tr_id 앞자리가 `V`로 변경됨 (예: TTTT1002U -> VTTT1002U)
- 시장가 주문 시 `OVRS_ORD_UNPR`에 "0"을 입력 (공란 불가)
- 모의투자에서는 지정가(00)만 가능
