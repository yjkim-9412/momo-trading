# 일(Day) 캔들

**Method:** GET
**URL:** `https://api.bithumb.com/v1/candles/days`
**인증:** 불필요

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| market | string | 필수 | 마켓 코드 (예: KRW-BTC) |
| to | string | 선택 | 마지막 캔들 시각 (exclusive). 비워서 요청 시 가장 최근 캔들 |
| count | integer | 선택 | 캔들 개수 (최대 200개). 기본값: 1 |
| convertingPriceUnit | string | 선택 | 가격 변환 통화 (원화 변환시 KRW) |

## 응답

| 필드 | 타입 | 설명 |
|------|------|------|
| market | string | 마켓 코드 |
| candle_date_time_utc | string | 캔들 시각 (UTC, ISO 8601) |
| candle_date_time_kst | string | 캔들 시각 (KST, ISO 8601) |
| opening_price | integer | 시가 |
| high_price | integer | 고가 |
| low_price | integer | 저가 |
| trade_price | integer | 종가 |
| timestamp | integer | Unix 타임스탬프 (밀리초) |
| candle_acc_trade_price | number | 누적 거래금액 |
| candle_acc_trade_volume | number | 누적 거래량 |
| prev_closing_price | integer | 전일 종가 |
| change_price | integer | 변화량 |
| change_rate | number | 변화율 |

응답은 객체 배열 형식입니다.

## 코드 예제

### Python
```python
import requests

url = "https://api.bithumb.com/v1/candles/days?count=1"
headers = {"accept": "application/json"}
response = requests.get(url, headers=headers)
print(response.json())
```

### JavaScript
```javascript
const options = {method: 'GET', headers: {accept: 'application/json'}};

fetch('https://api.bithumb.com/v1/candles/days?count=1', options)
  .then(response => response.json())
  .then(response => console.log(response))
  .catch(err => console.error(err));
```

### Java
```java
OkHttpClient client = new OkHttpClient();

Request request = new Request.Builder()
  .url("https://api.bithumb.com/v1/candles/days?count=1")
  .get()
  .addHeader("accept", "application/json")
  .build();

Response response = client.newCall(request).execute();
```

## 에러 응답

```json
{}
```

## 설명

일일 캔들 데이터를 조회합니다. API 키나 인증 토큰이 필요하지 않습니다.
