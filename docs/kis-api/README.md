# KIS Open API 레퍼런스

한국투자증권 Open API 로컬 레퍼런스. 공식 GitHub 레포(`koreainvestment/open-trading-api`)의 `examples_llm/` 샘플코드에서 추출.

## 도메인

| 구분 | URL |
|------|-----|
| 실전 | `https://openapi.koreainvestment.com:9443` |
| 모의 | `https://openapivts.koreainvestment.com:29443` |

## tr_id 규칙

- 실전: `T` 접두사 (예: `TTTC0081R`)
- 모의: `V` 접두사 (예: `VTTC0081R`)
- 시세 API는 실전/모의 동일 tr_id 사용

---

## 국내주식

### 주문/계좌 (`domestic/trading/`)

| API | 문서 | 실전 tr_id | 모의 tr_id |
|-----|------|-----------|-----------|
| 현금 주문 (매수) | [order_cash.md](domestic/trading/order_cash.md) | TTTC0012U | VTTC0012U |
| 현금 주문 (매도) | [order_cash.md](domestic/trading/order_cash.md) | TTTC0011U | VTTC0011U |
| 주문 정정/취소 | [order_rvsecncl.md](domestic/trading/order_rvsecncl.md) | TTTC0013U | VTTC0013U |
| 일별 체결 조회 (3개월이내) | [inquire_daily_ccld.md](domestic/trading/inquire_daily_ccld.md) | TTTC0081R | VTTC0081R |
| 일별 체결 조회 (3개월이전) | [inquire_daily_ccld.md](domestic/trading/inquire_daily_ccld.md) | CTSC9215R | VTSC9215R |
| 잔고 조회 | [inquire_balance.md](domestic/trading/inquire_balance.md) | TTTC8434R | VTTC8434R |
| 매수가능 조회 | [inquire_psbl_order.md](domestic/trading/inquire_psbl_order.md) | TTTC8908R | VTTC8908R |

### 시세 (`domestic/quotations/`)

| API | 문서 | tr_id |
|-----|------|-------|
| 현재가 시세 | [inquire_price.md](domestic/quotations/inquire_price.md) | FHKST01010100 |
| 호가/예상체결 | [inquire_asking_price_exp_ccn.md](domestic/quotations/inquire_asking_price_exp_ccn.md) | FHKST01010200 |
| 일자별 시세 | [inquire_daily_price.md](domestic/quotations/inquire_daily_price.md) | FHKST01010400 |
| 분봉 조회 | [inquire_time_itemchartprice.md](domestic/quotations/inquire_time_itemchartprice.md) | FHKST03010200 |
| 거래량 순위 | [volume_rank.md](domestic/quotations/volume_rank.md) | FHPST01710000 |
| 등락률 순위 | [fluctuation.md](domestic/quotations/fluctuation.md) | FHPST01700000 |

---

## 해외주식

### 주문/계좌 (`overseas/trading/`)

| API | 문서 | 실전 tr_id | 모의 tr_id |
|-----|------|-----------|-----------|
| 주문 | [order.md](overseas/trading/order.md) | 거래소별 12종 | 거래소별 12종 |
| 정정/취소 | [order_rvsecncl.md](overseas/trading/order_rvsecncl.md) | TTTT1004U | VTTT1004U |
| 잔고 조회 | [inquire_balance.md](overseas/trading/inquire_balance.md) | TTTS3012R | VTTS3012R |
| 체결 내역 | [inquire_ccnl.md](overseas/trading/inquire_ccnl.md) | TTTS3035R | VTTS3035R |
| 현재 잔고 | [inquire_present_balance.md](overseas/trading/inquire_present_balance.md) | CTRP6504R | VTRP6504R |
| 매수가능금액 | [inquire_psamount.md](overseas/trading/inquire_psamount.md) | TTTS3007R | VTTS3007R |
| 미체결 내역 | [inquire_nccs.md](overseas/trading/inquire_nccs.md) | TTTS3018R | VTTS3018R |
| 미국주간주문 | [daytime_order.md](overseas/trading/daytime_order.md) | TTTS6036U/6037U | — |
| 미국주간정정취소 | [daytime_order_rvsecncl.md](overseas/trading/daytime_order_rvsecncl.md) | TTTS6038U | — |
| 기간손익 | [inquire_period_profit.md](overseas/trading/inquire_period_profit.md) | TTTS3039R | VTTS3039R |
| 결제기준잔고 | [inquire_paymt_stdr_balance.md](overseas/trading/inquire_paymt_stdr_balance.md) | CTRP6010R | — |
| 예약주문 | [order_resv.md](overseas/trading/order_resv.md) | TTTT3014U 등 | — |

### 시세 (`overseas/quotations/`)

| API | 문서 | tr_id |
|-----|------|-------|
| 현재체결가 | [price.md](overseas/quotations/price.md) | HHDFS00000300 |
| 현재가상세 | [price_detail.md](overseas/quotations/price_detail.md) | HHDFS76200200 |
| 기간별시세 | [dailyprice.md](overseas/quotations/dailyprice.md) | HHDFS76240000 |
| 분봉 조회 | [inquire_time_itemchartprice.md](overseas/quotations/inquire_time_itemchartprice.md) | HHDFS76950200 |
| 일별차트 | [inquire_daily_chartprice.md](overseas/quotations/inquire_daily_chartprice.md) | FHKST03030100 |
| 지수 분봉 | [inquire_time_indexchartprice.md](overseas/quotations/inquire_time_indexchartprice.md) | FHKST03030200 |
| 조건검색 | [inquire_search.md](overseas/quotations/inquire_search.md) | HHDFS76410000 |
| 1호가 | [inquire_asking_price.md](overseas/quotations/inquire_asking_price.md) | HHDFS76200100 |
| 거래량급증 | [volume_surge.md](overseas/quotations/volume_surge.md) | HHDFS76270000 |
| 결제일자 | [countries_holiday.md](overseas/quotations/countries_holiday.md) | CTOS5011R |
