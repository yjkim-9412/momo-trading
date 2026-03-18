# KIS WebSocket 실시간 API

## 접속 정보

| 구분 | URL |
|------|-----|
| 실전 (국내) | `ws://ops.koreainvestment.com:21000` |
| 실전 (해외) | `ws://ops.koreainvestment.com:31000` |
| 모의 (국내) | `ws://ops.koreainvestment.com:31000` |
| 모의 (해외) | `ws://ops.koreainvestment.com:31000` |

**주의**: 모의투자는 국내/해외 모두 31000 포트 사용.

## 인증

WebSocket 접속 전 REST API로 접속키 발급:
- **URL**: `POST /oauth2/Approval`
- 요청: `{ "grant_type": "client_credentials", "appkey": "...", "appsecret": "..." }`
- 응답: `{ "approval_key": "..." }`

## 메시지 구조

### 구독 요청
```json
{
  "header": {
    "approval_key": "접속키",
    "custtype": "P",
    "tr_type": "1",
    "content-type": "utf-8"
  },
  "body": {
    "input": {
      "tr_id": "H0STCNT0",
      "tr_key": "005930"
    }
  }
}
```
- `tr_type`: "1" = 구독 등록, "2" = 구독 해제

### 수신 데이터
```
0|H0STCNT0|001|005930^135853^205000^...
```
- `0` = 암호화 여부 (0=평문, 1=암호화)
- `H0STCNT0` = tr_id
- `001` = 데이터 건수
- 이후 `^` 구분자로 필드 분리

---

## 실시간 API 목록

### 1. 국내주식 실시간 체결가 (시세)

| 항목 | 값 |
|------|-----|
| tr_id | `H0STCNT0` |
| tr_key | 종목코드 (예: `005930`) |
| 용도 | 현재가, 체결량, 등락률 실시간 수신 |

**현재 프로젝트에서 사용 중** (`trading/kis_websocket.py`)

주요 필드 (46개 중):
- [0] MKSC_SHRN_ISCD: 종목코드
- [1] STCK_CNTG_HOUR: 체결시각
- [2] STCK_PRPR: 현재가
- [3] PRDY_VRSS_SIGN: 전일대비부호
- [4] PRDY_VRSS: 전일대비
- [5] PRDY_CTRT: 전일대비율
- [12] ACML_VOL: 누적거래량
- [13] ACML_TR_PBMN: 누적거래대금

### 2. 해외주식 실시간 체결가 (시세)

| 항목 | 값 |
|------|-----|
| tr_id | `HDFSCNT0` |
| tr_key | `{거래소코드}{종목코드}` (예: `DNASTQQQ`, `DAMSSOXL`) |
| 용도 | 해외 현재가, 체결량 실시간 수신 |

**현재 프로젝트에서 사용 중** (`trading/kis_websocket.py`)

tr_key 거래소 접두사:
- `DNAS` = 나스닥 (NASD)
- `DNYS` = 뉴욕 (NYSE)
- `DAMS` = 아멕스 (AMEX)
- `DSHS` = 홍콩 (SEHK)
- `DTKS` = 일본 (TKSE)

주요 필드 (26개 중):
- [0] RSYM: 실시간종목코드
- [1] SYMB: 종목코드
- [11] LAST: 현재가
- [12] SIGN: 전일대비부호
- [13] DIFF: 전일대비
- [14] RATE: 등락률
- [15] TVOL: 거래량
- [16] TAMT: 거래대금

### 3. 국내주식 실시간 체결통보 (주문)

| 항목 | 값 |
|------|-----|
| tr_id (실전) | `H0STCNI0` |
| tr_id (모의) | `H0STCNI9` |
| tr_key | HTS ID (종목코드 아님!) |
| 용도 | 주문 접수/체결/거부 실시간 통보 |
| 암호화 | AES256 (암호화 키는 첫 수신 시 제공) |

**현재 미구현** — 체결 확인을 REST polling으로 처리 중

주요 필드 (26개):
- [0] CUST_ID: 고객 ID
- [1] ACNT_NO: 계좌번호
- [2] ODER_NO: 주문번호
- [3] OODER_NO: 원주문번호
- [4] SELN_BYOV_CLS: 매도매수구분 (01=매도, 02=매수)
- [8] STCK_SHRN_ISCD: 종목코드
- [9] CNTG_QTY: 체결수량
- [10] CNTG_UNPR: 체결단가
- [11] STCK_CNTG_HOUR: 체결시각
- [12] RFUS_YN: 거부여부
- [13] CNTG_YN: 체결여부 (**1=주문접수, 2=체결**)
- [14] ACPT_YN: 접수여부
- [16] ODER_QTY: 주문수량
- [25] ODER_PRC: 주문가격

**핵심**: `CNTG_YN == "2"`이면 체결 완료. 이때 `CNTG_QTY`와 `CNTG_UNPR`로 체결 정보 확보.

### 4. 해외주식 실시간 체결통보 (주문)

| 항목 | 값 |
|------|-----|
| tr_id (실전) | `H0GSCNI0` |
| tr_id (모의) | `H0GSCNI9` |
| tr_key | HTS ID (종목코드 아님!) |
| 용도 | 해외주문 접수/체결/거부 실시간 통보 |
| 암호화 | AES256 |

**현재 미구현**

주요 필드 (31개):
- [0] CUST_ID: 고객 ID
- [1] ACNT_NO: 계좌번호
- [2] ODER_NO: 주문번호
- [3] OODER_NO: 원주문번호
- [4] SELN_BYOV_CLS: 매도매수구분
- [8] STCK_SHRN_ISCD: 종목코드
- [9] CNTG_QTY: 체결수량
- [10] CNTG_UNPR: 체결단가
- [11] STCK_CNTG_HOUR: 체결시각
- [13] CNTG_YN: 체결여부 (1=접수, 2=체결)
- [16] ODER_QTY: 주문수량

---

## 구독 제한

- 최대 동시 구독: **40건** (시세 + 체결통보 합산)
- 체결통보는 HTS ID 1건으로 계좌 전체 주문 통보 수신 가능 (종목별 구독 불필요)
- 시세는 종목별 구독 필요

## AES256 복호화 (체결통보)

체결통보 데이터는 암호화되어 수신됨:
1. 첫 메시지 수신 시 `iv`, `key` 제공
2. AES-256-CBC로 복호화
3. 복호화된 데이터는 `^` 구분자로 파싱

```python
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64

def decrypt_aes256(data: str, key: str, iv: str) -> str:
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    decrypted = unpad(cipher.decrypt(base64.b64decode(data)), AES.block_size)
    return decrypted.decode('utf-8')
```
