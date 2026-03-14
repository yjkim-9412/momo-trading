# 분(Minute) 캔들

**Method:** GET
**URL:** `https://api.bithumb.com/v1/candles/minutes/{unit}`
**인증:** 불필요

## 파라미터

| 이름 | 위치 | 타입 | 필수 | 설명 |
|------|------|------|------|------|
| unit | 경로 | Integer | 필수 | 분 단위. 가능한 값: 1, 3, 5, 10, 15, 30, 60, 240 |
| market | 쿼리 | String | 필수 | 마켓 코드 (예: KRW-BTC) |
| to | 쿼리 | String | 선택 | 마지막 캔들 시각 (exclusive). 비워서 요청 시 가장 최근 캔들 |
| count | 쿼리 | Integer | 선택 | 캔들 개수 (최대 200개까지 요청 가능) |

## 응답

| 필드 | 타입 | 설명 |
|------|------|------|
| market | string | 마켓 코드 (예: KRW-BTC) |
| candle_date_time_utc | string | 캔들 시각 (UTC) (예: 2018-04-18T10:16:00) |
| candle_date_time_kst | string | 캔들 시각 (KST) (예: 2018-04-18T19:16:00) |
| opening_price | integer | 시가 |
| high_price | integer | 고가 |
| low_price | integer | 저가 |
| trade_price | integer | 종가 |
| timestamp | integer | Unix 타임스탬프 |
| candle_acc_trade_price | number | 누적 거래금액 |
| candle_acc_trade_volume | number | 누적 거래량 |
| unit | integer | 캔들 단위 |

응답은 객체 배열 형식입니다.

## 코드 예제

### Python
```python
import requests

url = "https://api.bithumb.com/v1/candles/minutes/1?market=KRW-BTC&count=1"
headers = {"accept": "application/json"}
response = requests.get(url, headers=headers)
print(response.json())
```

### JavaScript
```javascript
const options = {method: 'GET', headers: {accept: 'application/json'}};

fetch('https://api.bithumb.com/v1/candles/minutes/1?market=KRW-BTC&count=1', options)
  .then(response => response.json())
  .then(response => console.log(response))
  .catch(err => console.error(err));
```

### Java
```java
OkHttpClient client = new OkHttpClient();

Request request = new Request.Builder()
  .url("https://api.bithumb.com/v1/candles/minutes/1?market=KRW-BTC&count=1")
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
