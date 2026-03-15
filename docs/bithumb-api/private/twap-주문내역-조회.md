# TWAP 주문 내역 조회

**Method:** GET
**URL:** `https://api.bithumb.com/v1/twap`
**인증:** Bearer JWT 토큰

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| Authorization | Header | Yes | JWT Bearer token |

## 응답

응답은 TWAP 주문 객체 리스트를 반환합니다. 각 TWAP 주문 객체는 주문 내역 정보를 포함합니다.

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

query = ''
hash_obj = hashlib.sha512()
hash_obj.update(query.encode())
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

response = requests.get(apiUrl + '/v1/twap', headers=headers)
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

const query = '';
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

axios.get(apiUrl + '/v1/twap', config)
  .then(response => console.log(response.data))
  .catch(error => console.error(error));
```

### Java
```java
import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;
import org.apache.http.client.methods.HttpGet;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import java.util.UUID;

String accessKey = "YOUR_API_KEY";
String secretKey = "YOUR_SECRET_KEY";
String apiUrl = "https://api.bithumb.com";

Algorithm algorithm = Algorithm.HMAC256(secretKey);
String jwtToken = JWT.create()
  .withClaim("access_key", accessKey)
  .withClaim("nonce", UUID.randomUUID().toString())
  .withClaim("timestamp", System.currentTimeMillis())
  .withClaim("query_hash", "")
  .withClaim("query_hash_alg", "SHA512")
  .sign(algorithm);

HttpGet request = new HttpGet(apiUrl + "/v1/twap");
request.addHeader("Authorization", "Bearer " + jwtToken);
```

## 인증

JWT Token Generation:
- Required Claims: access_key, nonce, timestamp, query_hash, query_hash_alg
- Hash Algorithm: SHA-512
- Token Format: `Bearer {jwtToken}`

## 주의사항

- This is a private API endpoint requiring authentication
- Returns a list of TWAP order objects
- HTTP Status Codes: 200 (Success), 400 (Bad request), 401 (Unauthorized), 404 (Not found)
