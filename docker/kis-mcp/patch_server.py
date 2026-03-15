"""Patch cloned KIS_MCP_Server sources for MOMO runtime expectations."""
from pathlib import Path
import sys


ACNT_OLD = '"ACNT_PRDT_CD": "01"'
ACNT_NEW = '"ACNT_PRDT_CD": os.environ.get("KIS_PROD_TYPE", "01")'

IMPORT_OLD = "import json\nimport logging\n"
IMPORT_NEW = "import asyncio\nimport json\nimport logging\n"

TOKEN_BLOCK_OLD = """# Token storage
TOKEN_FILE = Path(__file__).resolve().parent / "token.json"

def load_token():
    \"\"\"Load token from file if it exists and is not expired\"\"\"
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, 'r') as f:
                token_data = json.load(f)
                expires_at = datetime.fromisoformat(token_data['expires_at'])
                if datetime.now() < expires_at:
                    return token_data['token'], expires_at
        except Exception as e:
            print(f"Error loading token: {e}", file=sys.stderr)
    return None, None

def save_token(token: str, expires_at: datetime):
    \"\"\"Save token to file\"\"\"
    try:
        with open(TOKEN_FILE, 'w') as f:
            json.dump({
                'token': token,
                'expires_at': expires_at.isoformat()
            }, f)
    except Exception as e:
        print(f"Error saving token: {e}", file=sys.stderr)

async def get_access_token(client: httpx.AsyncClient) -> str:
    \"\"\"
    Get access token with file-based caching
    Returns cached token if valid, otherwise requests new token
    \"\"\"
    token, expires_at = load_token()
    if token and expires_at and datetime.now() < expires_at:
        return token
    
    token_response = await client.post(
        f"{DOMAIN}{TOKEN_PATH}",
        headers={"content-type": CONTENT_TYPE},
        json={
            "grant_type": "client_credentials",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"]
        }
    )
    
    if token_response.status_code != 200:
        raise Exception(f"Failed to get token: {token_response.text}")
    
    token_data = token_response.json()
    token = token_data["access_token"]
    
    expires_at = datetime.now() + timedelta(hours=23)
    save_token(token, expires_at)
    
    return token
"""

TOKEN_BLOCK_NEW = """# Token storage
TOKEN_FILE = Path(__file__).resolve().parent / "token.json"
_token_lock = asyncio.Lock()
_cached_token = None
_cached_expires_at = None

def load_token():
    \"\"\"Load token from file if it exists and is not expired\"\"\"
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, 'r') as f:
                token_data = json.load(f)
                expires_at = datetime.fromisoformat(token_data['expires_at'])
                if datetime.now() < expires_at:
                    return token_data['token'], expires_at
        except Exception as e:
            print(f"Error loading token: {e}", file=sys.stderr)
    return None, None

def save_token(token: str, expires_at: datetime):
    \"\"\"Save token to file\"\"\"
    try:
        with open(TOKEN_FILE, 'w') as f:
            json.dump({
                'token': token,
                'expires_at': expires_at.isoformat()
            }, f)
    except Exception as e:
        print(f"Error saving token: {e}", file=sys.stderr)

async def get_access_token(client: httpx.AsyncClient) -> str:
    \"\"\"
    Get access token with in-process serialization to avoid EGW00133 bursts.
    Returns cached token if valid, otherwise requests new token.
    \"\"\"
    global _cached_token, _cached_expires_at

    if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
        return _cached_token

    for attempt in range(2):
        async with _token_lock:
            if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
                return _cached_token

            token, expires_at = load_token()
            if token and expires_at and datetime.now() < expires_at:
                _cached_token = token
                _cached_expires_at = expires_at
                return token

            token_response = await client.post(
                f"{DOMAIN}{TOKEN_PATH}",
                headers={"content-type": CONTENT_TYPE},
                json={
                    "grant_type": "client_credentials",
                    "appkey": os.environ["KIS_APP_KEY"],
                    "appsecret": os.environ["KIS_APP_SECRET"]
                }
            )

            if token_response.status_code == 200:
                token_data = token_response.json()
                token = token_data["access_token"]
                expires_at = datetime.now() + timedelta(hours=23)
                _cached_token = token
                _cached_expires_at = expires_at
                save_token(token, expires_at)
                return token

            response_text = token_response.text
            if "EGW00133" not in response_text or attempt > 0:
                raise Exception(f"Failed to get token: {response_text}")

            logger.warning("KIS token issuance limited (EGW00133), retrying after 60 seconds")

        await asyncio.sleep(60)

    raise Exception("Failed to get token: issuance retry exhausted")
"""


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label} 패턴을 1회 기대했지만 {count}회 발견했습니다.")
    return text.replace(old, new, 1)


def patch_server(path: Path) -> int:
    text = path.read_text(encoding="utf-8")

    if "import asyncio\n" not in text:
        text = _replace_once(text, IMPORT_OLD, IMPORT_NEW, label="asyncio import")

    acnt_count = text.count(ACNT_OLD)
    if acnt_count == 0:
        raise RuntimeError("server.py에서 ACNT_PRDT_CD 하드코딩을 찾지 못했습니다.")
    text = text.replace(ACNT_OLD, ACNT_NEW)

    text = _replace_once(text, TOKEN_BLOCK_OLD, TOKEN_BLOCK_NEW, label="token block")

    path.write_text(text, encoding="utf-8")
    return acnt_count


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: patch_server.py /path/to/server.py")
    count = patch_server(Path(argv[1]))
    print(f"patched ACNT_PRDT_CD occurrences: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
