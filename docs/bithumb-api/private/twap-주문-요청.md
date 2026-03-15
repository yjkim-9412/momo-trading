# TWAP 주문 요청

**Method:** POST
**URL:** `https://api.bithumb.com/v1/twap`
**인증:** Bearer JWT 토큰

## 파라미터

### 헤더

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| Authorization | string | Yes | Bearer JWT token |
| Content-Type | string | Yes | application/json |

### 요청 본문

TWAP (Time-Weighted Average Price) 주문 요청을 위한 본문 스키마입니다. 구체적인 파라미터 목록은 공식 문서를 참고하세요.

## 응답

| 필드 | 타입 | 설명 |
|------|------|------|
| uuid | string | Order unique identifier |
| side | string | "bid" or "ask" |
| ord_type | string | Order type |
| price | string | Order price |
| state | string | Order status |
| market | string | Trading pair symbol |
| created_at | string | ISO timestamp |
| volume | string | Order quantity |
| remaining_volume | string | Unfilled quantity |
| reserved_fee | string | Reserved fee amount |
| remaining_fee | string | Remaining fee |
| paid_fee | string | Used fee |
| locked | string | Amount in use |
| executed_volume | string | Filled quantity |
| trades_count | integer | Number of executions |

## 코드 예제

### Python
```python
import jwt
import uuid
import hashlib
import time
import json
import requests
from urllib.parse import urlencode

accessKey = 'YOUR_API_KEY'
secretKey = 'YOUR_SECRET_KEY'
apiUrl = 'https://api.bithumb.com'

requestBody = {
  'market': 'KRW-BTC',
  'side': 'bid',
  'volume': 0.001,
  'price': 84000000,
  'ord_type': 'limit'
}

query = urlencode(requestBody).encode()
queryHash = hashlib.sha512(query).hexdigest()
payload = {
  'access_key': accessKey,
  'nonce': str(uuid.uuid4()),
  'timestamp': round(time.time() * 1000),
  'query_hash': queryHash,
  'query_hash_alg': 'SHA512'
}

jwt_token = jwt.encode(payload, secretKey)
headers = {
  'Authorization': f'Bearer {jwt_token}',
  'Content-Type': 'application/json'
}

response = requests.post(apiUrl + '/v1/twap',
                        data=json.dumps(requestBody),
                        headers=headers)
print(response.json())
```

### JavaScript
```javascript
const jwt = require('jsonwebtoken');
const { v4: uuidv4 } = require('uuid');
const crypto = require('crypto');
const axios = require('axios');

const accessKey = 'YOUR_API_KEY';
const secretKey = 'YOUR_SECRET_KEY';
const apiUrl = 'https://api.bithumb.com';

const requestBody = {
  market: 'KRW-BTC',
  side: 'bid',
  volume: 0.001,
  price: 84000000,
  ord_type: 'limit'
};

const query = require('querystring').encode(requestBody);
const hash = crypto.createHash('SHA512').update(query, 'utf-8').digest('hex');
const payload = {
  access_key: accessKey,
  nonce: uuidv4(),
  timestamp: Date.now(),
  query_hash: hash,
  query_hash_alg: 'SHA512'
};

const jwtToken = jwt.sign(payload, secretKey);
const config = {
  headers: {
    Authorization: `Bearer ${jwtToken}`,
    'Content-Type': 'application/json'
  }
};

axios.post(apiUrl + '/v1/twap', requestBody, config)
  .then(response => console.log(response.data))
  .catch(error => console.error(error.response.data));
```

### Java
```java
import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;
import org.apache.http.client.methods.HttpPost;
import org.apache.http.client.utils.URLEncodedUtils;
import org.apache.http.entity.StringEntity;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.message.BasicNameValuePair;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.math.BigInteger;

String accessKey = "YOUR_API_KEY";
String secretKey = "YOUR_SECRET_KEY";
String apiUrl = "https://api.bithumb.com";

Map<String, Object> requestBody = new LinkedHashMap<>();
requestBody.put("market", "KRW-BTC");
requestBody.put("side", "bid");
requestBody.put("volume", 0.001);
requestBody.put("price", 84000000);
requestBody.put("ord_type", "limit");

List<BasicNameValuePair> queryParams = requestBody.entrySet().stream()
  .map(e -> new BasicNameValuePair(e.getKey(), String.valueOf(e.getValue())))
  .toList();
String query = URLEncodedUtils.format(queryParams, StandardCharsets.UTF_8);
MessageDigest md = MessageDigest.getInstance("SHA-512");
String queryHash = String.format("%0128x", new BigInteger(1, md.digest(query.getBytes())));

Algorithm algorithm = Algorithm.HMAC256(secretKey);
String jwtToken = JWT.create()
  .withClaim("access_key", accessKey)
  .withClaim("nonce", UUID.randomUUID().toString())
  .withClaim("timestamp", System.currentTimeMillis())
  .withClaim("query_hash", queryHash)
  .withClaim("query_hash_alg", "SHA512")
  .sign(algorithm);

HttpPost request = new HttpPost(apiUrl + "/v1/twap");
request.addHeader("Authorization", "Bearer " + jwtToken);
request.addHeader("Content-type", "application/json");
request.setEntity(new StringEntity(new ObjectMapper().writeValueAsString(requestBody)));

CloseableHttpClient client = HttpClients.createDefault();
CloseableHttpResponse response = client.execute(request);
```

## 인증

JWT Token Generation:
- Required Claims: access_key, nonce, timestamp, query_hash, query_hash_alg
- Hash Algorithm: SHA-512 (for request body)
- Token Format: `Bearer {jwtToken}`
- Algorithm: HMAC256 with secret key

## 주의사항

- Response status code: 201 Created
- TWAP orders execute over time at weighted average price
- Full TWAP-specific parameter details available in official documentation
- Authentication follows Bithumb's standard JWT protocol
- Response format consistent with other order creation endpoints
