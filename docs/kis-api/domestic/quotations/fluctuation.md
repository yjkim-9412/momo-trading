# 등락률 순위

**URL:** `/uapi/domestic-stock/v1/ranking/fluctuation`
**Method:** GET
**분류:** [국내주식] 순위분석 > 등락률 순위[v1_국내주식-088]

## tr_id

| 구분 | tr_id | 비고 |
|------|-------|------|
| 공통 | FHPST01700000 | 실전/모의 동일 |

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| fid_cond_mrkt_div_code | string | Yes | 조건 시장 분류 코드 (J:KRX, NX:NXT) |
| fid_cond_scr_div_code | string | Yes | 조건 화면 분류 코드 (20170 고정) |
| fid_input_iscd | string | Yes | 입력 종목코드 (0000:전체) |
| fid_rank_sort_cls_code | string | Yes | 순위 정렬 구분 코드 (0000:등락률순) |
| fid_input_cnt_1 | string | Yes | 입력 수1 (조회할 종목 수) |
| fid_prc_cls_code | string | Yes | 가격 구분 코드 (0:전체) |
| fid_input_price_1 | string | Yes | 입력 가격1 (하한가) |
| fid_input_price_2 | string | Yes | 입력 가격2 (상한가) |
| fid_vol_cnt | string | Yes | 거래량 수 (최소 거래량) |
| fid_trgt_cls_code | string | Yes | 대상 구분 코드 (9자리, 각 자리 "1" 또는 "0": 증거금30%/40%/50%/60%/100%/신용보증금30%/40%/50%/60%) |
| fid_trgt_exls_cls_code | string | Yes | 대상 제외 구분 코드 (10자리, 각 자리 "1" 또는 "0": 투자위험경고주의/관리종목/정리매매/불성실공시/우선주/거래정지/ETF/ETN/신용주문불가/SPAC) |
| fid_div_cls_code | string | Yes | 분류 구분 코드 (0:전체) |
| fid_rsfl_rate1 | string | Yes | 등락 비율1 (하락률 하한) |
| fid_rsfl_rate2 | string | Yes | 등락 비율2 (상승률 상한) |

## 응답

- **output** (array): 등락률 순위 목록

## 페이징

- 연속조회: `tr_cont`가 "M"이면 다음 페이지 존재

## 비고

- 파라미터 key가 소문자(lowercase)로 전달됨에 주의
