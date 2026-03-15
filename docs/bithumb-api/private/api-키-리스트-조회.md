# api-키-리스트-조회

**Method:** GET
**URL:** `https://api.bithumb.com/v1/api_keys`
**인증:** Bearer JWT 토큰

## 파라미터

| 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|
| Authorization | header | 필수 | JWT 인증 토큰 |

## 응답

```json
[
  {
    "api_key": "string",
    "created_at": "string",
    "expired_at": "string",
    "name": "string",
    "ip_addresses": ["string"],
    "permissions": ["string"]
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| api_key | string | API 키 |
| created_at | string | 생성 시간 (ISO 8601) |
| expired_at | string | 만료 시간 (ISO 8601) |
| name | string | API 키 이름 |
| ip_addresses | array | 허용된 IP 주소 목록 |
| permissions | array | 권한 목록 |

## 인증

**방식:** JWT (JSON Web Token)

**필수 클레임:**
- `access_key` - API 키
- `nonce` - UUID
- `timestamp` - 현재 시간 (밀리초)
- `query_hash` - 쿼리 문자열의 SHA512 해시
- `query_hash_alg` - "SHA512"

## 설명

계정과 연결된 모든 API 키를 만료 날짜와 함께 검색합니다.

## 코드 예제

### Python
```python
import jwt
import uuid
import time
import requests

accessKey = 'your_api_key'
secretKey = 'your_secret_key'
apiUrl = 'https://api.bithumb.com'

payload = {
  'access_key': accessKey,
  'nonce': str(uuid.uuid4()),
  'timestamp': round(time.time() * 1000)
}
jwt_token = jwt.encode(payload, secretKey)
headers = {'Authorization': f'Bearer {jwt_token}'}

response = requests.get(apiUrl + '/v1/api_keys', headers=headers)
print(response.json())
```

### JavaScript
```javascript
const jwt = require('jsonwebtoken');
const { v4: uuidv4 } = require('uuid');
const axios = require('axios');

const accessKey = 'your_api_key';
const secretKey = 'your_secret_key';
const apiUrl = 'https://api.bithumb.com';

const payload = {
  access_key: accessKey,
  nonce: uuidv4(),
  timestamp: Date.now()
};
const jwtToken = jwt.sign(payload, secretKey);
const config = {
  headers: { Authorization: `Bearer ${jwtToken}` }
};

axios.get(apiUrl + '/v1/api_keys', config)
  .then(response => console.log(response.data))
  .catch(error => console.error(error.response.data));
```

### Java
```java
import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;
import org.apache.http.client.methods.HttpGet;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import java.util.UUID;

String accessKey = "your_api_key";
String secretKey = "your_secret_key";
String apiUrl = "https://api.bithumb.com";

Algorithm algorithm = Algorithm.HMAC256(secretKey);
String jwtToken = JWT.create()
  .withClaim("access_key", accessKey)
  .withClaim("nonce", UUID.randomUUID().toString())
  .withClaim("timestamp", System.currentTimeMillis())
  .sign(algorithm);

HttpGet request = new HttpGet(apiUrl + "/v1/api_keys");
request.addHeader("Authorization", "Bearer " + jwtToken);

CloseableHttpClient client = HttpClients.createDefault();
CloseableHttpResponse response = client.execute(request);
```

## 추가 참고

- 이 엔드포인트는 계정과 연결된 모든 API 키를 만료 날짜와 함께 검색합니다.
- 올바른 JWT 인증이 필요합니다.
- 인증 �더 이상의 쿼리 파라미터가 필요하지 않습니다.
- 응답은 메타데이터가 포함된 API 키 객체 배열을 반환합니다.
