# 해외주식 예약주문접수

**URL:** `/uapi/overseas-stock/v1/trading/order-resv`
**Method:** POST
**API ID:** v1_해외주식-002

## tr_id

### 실전투자

| 구분 | tr_id | 비고 |
|------|-------|------|
| 미국 매수 | TTTT3014U | |
| 미국 매도 | TTTT3016U | MOO(장개시시장가) 가능 |
| 아시아 | TTTS3013U | 홍콩/중국/일본/베트남 |

### 모의투자

| 구분 | tr_id | 비고 |
|------|-------|------|
| 미국 매수 | VTTT3014U | |
| 미국 매도 | VTTT3016U | |
| 아시아 | VTTS3013U | |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| CANO | string | Yes | 종합계좌번호 |
| ACNT_PRDT_CD | string | Yes | 계좌상품코드 |
| PDNO | string | Yes | 상품번호 (종목코드) |
| OVRS_EXCG_CD | string | Yes | 해외거래소코드 (NASD/NYSE/AMEX/SEHK/SHAA/SZAA/TKSE/HASE/VNSE) |
| FT_ORD_QTY | string | Yes | FT주문수량 |
| FT_ORD_UNPR3 | string | Yes | FT주문단가3 |
| SLL_BUY_DVSN_CD | string | 아시아만 | 매도매수구분코드 (01: 매도, 02: 매수) |
| RVSE_CNCL_DVSN_CD | string | 아시아만 | 정정취소구분코드 (00: 매도/매수) |
| PRDT_TYPE_CD | string | 아시아만 | 상품유형코드 |
| ORD_SVR_DVSN_CD | string | No | 주문서버구분코드 ("0") |
| RSVN_ORD_RCIT_DT | string | 아시아만 | 예약주문접수일자 |
| ORD_DVSN | string | 미국만 | 주문구분 |
| OVRS_RSVN_ODNO | string | 아시아만 | 해외예약주문번호 |
| ALGO_ORD_TMD_DVSN_CD | string | No | 알고리즘주문시간구분코드 (TWAP/VWAP: "02" 고정) |

## 예약주문 접수 가능 시간

### 미국
- 10:00 ~ 23:20 (서머타임 시 10:00 ~ 22:20)
- 주문제한: 16:30 ~ 16:45 경 (시스템 정산작업시간)
- 23:30 정규장으로 주문 전송 (서머타임 시 22:30)

### 홍콩
- 09:00 ~ 10:20 접수 -> 10:30 주문전송
- 10:40 ~ 13:50 접수 -> 14:00 주문전송

### 중국
- 09:00 ~ 10:20 접수 -> 10:30 주문전송
- 10:40 ~ 13:50 접수 -> 14:00 주문전송

### 일본
- 09:10 ~ 12:20 접수 -> 12:30 주문전송

### 베트남
- 09:00 ~ 11:00 접수 -> 11:15 주문전송
- 11:20 ~ 14:50 접수 -> 15:00 주문전송

## 비고

- 미국거래소 운영시간 외 미국주식을 예약 매매하기 위한 API
- 해외주식 서비스 신청 후 이용 가능
- 지정가주문만 가능 (단, 미국 예약매도 TTTT3016U는 MOO 장개시시장가 가능)
- 예약주문 유효기간: 당일 (미국장 마감 후 미체결주문은 자동취소)
- 증거금 및 잔고보유 체크 안함
- POST API이므로 BODY 값의 key를 대문자로 작성
