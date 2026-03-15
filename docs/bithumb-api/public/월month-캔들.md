# 월(Month) 캔들

**Method:** GET
**URL:** `https://api.bithumb.com/v1/candles/months`
**인증:** 불필요

## 파라미터

| 이름 | 위치 | 타입 | 필수 | 설명 |
|------|------|------|------|------|
| market | 쿼리 | string | 필수 | 마켓 코드 (예: KRW-BTC) |
| to | 쿼리 | string | 선택 | 마지막 캔들 시각 (exclusive). 비워서 요청 시 가장 최근 캔들 |
| count | 쿼리 | integer | 선택 | 캔들 개수 (최대 200개까지 요청 가능) |

## 응답

| 필드 | 타입 | 설명 |
|------|------|------|
| market | string | 거래 대상 페어의 고유 심볼 (예: KRW-BTC) |
| candle_date_time_utc | string | UTC 시간 (예: 2018-04-16T00:00:00) |
| candle_date_time_kst | string | KST 시간 (예: 2018-04-16T09:00:00) |
| opening_price | integer | 시가 |
| high_price | integer | 고가 |
| low_price | integer | 저가 |
| trade_price | integer | 종가 |
| timestamp | integer | Unix 타임스탬프 |
| candle_acc_trade_price | number | 누적 거래 금액 |
| candle_acc_trade_volume | number | 누적 거래량 |
| first_day_of_period | string | 해당 월의 첫날 |

응답은 객체 배열 형식입니다.

## 코드 예제

### Python
```python
import requests

url = "https://api.bithumb.com/v1/candles/months?count=1"
headers = {"accept": "application/json"}
response = requests.get(url, headers=headers)
print(response.json())
```

### JavaScript
```javascript
const options = {method: 'GET', headers: {accept: 'application/json'}};

fetch('https://api.bithumb.com/v1/candles/months?count=1', options)
 .then(response => response.json())
 .then(response => console.log(response))
 .catch(err => console.error(err));
```

### Java
```java
OkHttpClient client = new OkHttpClient();

Request request = new Request.Builder()
 .url("https://api.bithumb.com/v1/candles/months?count=1")
 .get()
 .addHeader("accept", "application/json")
 .build();

Response response = client.newCall(request).execute();
```

## 에러 응답

```json
{
  "error": {}
}
```

## 설명

- 요청당 최대 200개의 캔들 조회 가능
- 월간 OHLCV 데이터를 제공합니다
- 'to' 파라미터를 생략하면 가장 최근 데이터를 조회합니다
