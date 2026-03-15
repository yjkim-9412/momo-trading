# TWAP 주문 취소

**Method:** DELETE
**URL:** `https://api.bithumb.com/v1/twap`
**인증:** Bearer JWT 토큰

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| Authorization | Header | Yes | JWT Bearer token |
| uuid | Query | Yes | TWAP Order UUID |

## 응답

응답은 취소된 TWAP 주문의 상세 정보를 반환합니다.

## 에러 응답

```json
{
  "error": {
    "name": "error name",
    "message": "error message"
  }
}
```

## 코드 예제

### Python
```python
import jwt
import uuid
import hashlib
import time
import requests

accessKey = 'YOUR_API_KEY'
secretKey = 'YOUR_SECRET_KEY'
apiUrl = 'https://api.bithumb.com'

param = dict(uuid='YOUR_TWAP_ORDER_UUID')
query = '&'.join([f'{k}={v}' for k, v in param.items()])

hash_obj = hashlib.sha512()
hash_obj.update(query.encode('utf-8'))
query_hash = hash_obj.hexdigest()

payload = {
    'access_key': accessKey,
    'nonce': str(uuid.uuid4()),
    'timestamp': round(time.time() * 1000),
    'query_hash': query_hash,
    'query_hash_alg': 'SHA512'
}

jwt_token = jwt.encode(payload, secretKey)
headers = {'Authorization': f'Bearer {jwt_token}'}

response = requests.delete(apiUrl + '/v1/twap', params=param, headers=headers)
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

const query = 'uuid=YOUR_TWAP_ORDER_UUID';
const alg = 'SHA512';
const hash = crypto.createHash(alg);
const queryHash = hash.update(query, 'utf-8').digest('hex');

const payload = {
  access_key: accessKey,
  nonce: uuidv4(),
  timestamp: Date.now(),
  query_hash: queryHash,
  query_hash_alg: alg
};

const jwtToken = jwt.sign(payload, secretKey);
const config = {
  headers: { Authorization: `Bearer ${jwtToken}` }
};

axios.delete(apiUrl + '/v1/twap?' + query, config)
  .then(response => console.log(response.data))
  .catch(error => console.error(error.response.data));
```

### Java
```java
import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;
import org.apache.http.client.methods.HttpDelete;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.util.EntityUtils;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

String accessKey = "YOUR_API_KEY";
String secretKey = "YOUR_SECRET_KEY";
String apiUrl = "https://api.bithumb.com";
String query = "uuid=YOUR_TWAP_ORDER_UUID";

MessageDigest md = MessageDigest.getInstance("SHA-512");
md.update(query.getBytes(StandardCharsets.UTF_8));
String queryHash = String.format("%0128x", new BigInteger(1, md.digest()));

Algorithm algorithm = Algorithm.HMAC256(secretKey);
String jwtToken = JWT.create()
    .withClaim("access_key", accessKey)
    .withClaim("nonce", UUID.randomUUID().toString())
    .withClaim("timestamp", System.currentTimeMillis())
    .withClaim("query_hash", queryHash)
    .withClaim("query_hash_alg", "SHA512")
    .sign(algorithm);

HttpDelete httpRequest = new HttpDelete(apiUrl + "/v1/twap?" + query);
httpRequest.addHeader("Authorization", "Bearer " + jwtToken);

try (CloseableHttpClient client = HttpClients.createDefault();
     var response = client.execute(httpRequest)) {
    System.out.println(EntityUtils.toString(response.getEntity()));
}
```

## 인증

JWT Token Generation:
- Required Claims: access_key, nonce, timestamp, query_hash, query_hash_alg
- Hash Algorithm: SHA-512
- Token Format: `Bearer {jwtToken}`

## 주의사항

- TWAP (Time-Weighted Average Price) orders require proper UUID identification
- Authentication is mandatory for this private endpoint
- Query hash generation is required for request signing
- HTTP Status Codes: 200 (Success), 400 (Bad request), 401 (Unauthorized), 404 (Not found)
