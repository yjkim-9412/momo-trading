# 설치 및 설정 가이드

> 일반 개요는 [README](../README.md)를 참조하세요.

---

## 사전 요구사항

- Python 3.12+
- Docker & Docker Compose (KIS MCP 서버 실행용)
- [KIS Developers](https://apiportal.koreainvestment.com/) 계정 및 API 키 (주식 매매 시)
- [빗썸](https://www.bithumb.com/) 계정 및 API 키 (코인 매매 시)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 또는 Codex CLI (LLM 분석용)

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| Web Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| DB Migration | Alembic |
| Validation | Pydantic v2 |
| 기술적 분석 | pandas + pandas-ta |
| 실시간 통신 | WebSocket (KIS/빗썸), SSE (Admin) |
| 스케줄러 | APScheduler |
| 증권사 API | KIS MCP Server (Docker) + KIS REST API + Bithumb REST/WebSocket |
| LLM | Claude Code CLI / Codex CLI |
| 로깅 | loguru |
| 테스트 | pytest + pytest-asyncio |

---

## 1. 저장소 클론 및 환경 설정

```bash
git clone https://github.com/jjunmo/momo-trading.git
cd momo-trading

# 가상환경
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

---

## 2. 환경 변수 설정

### 주식 (KRX) 설정

```bash
cp .env.example .env
```

`.env` 파일을 열고 KIS API 키와 LLM provider를 설정합니다:

```bash
# === 필수: KIS API 인증 ===
KIS_APP_KEY=your_app_key              # 실전 투자 앱 키
KIS_APP_SECRET=your_app_secret        # 실전 투자 앱 시크릿
KIS_PAPER_APP_KEY=your_paper_key      # 모의 투자 앱 키
KIS_PAPER_APP_SECRET=your_paper_secret # 모의 투자 앱 시크릿
KIS_ACCT_STOCK=your_account_number    # 실전 계좌번호 (10자리 권장)
KIS_PAPER_STOCK=your_paper_account    # 모의 계좌번호 (10자리 권장)
KIS_PROD_TYPE=                        # 2자리 상품코드 override (8자리 CANO만 입력 시 필수)
KIS_ACCOUNT_TYPE=VIRTUAL              # VIRTUAL(모의) 또는 REAL(실전)

# === LLM Provider 선택 ===
LLM_PROVIDER=CLAUDE_CODE              # CLAUDE_CODE 또는 CODEX_CLI

# Claude Code CLI
CLAUDE_CODE_MODEL=sonnet
CLAUDE_CODE_MODEL_TIER1=haiku
CLAUDE_CODE_MODEL_TIER2=sonnet

# Codex CLI
CODEX_MODEL=gpt-5.4
CODEX_REASONING_EFFORT_TIER2=xhigh

# === 거래 안전 설정 ===
TRADING_ENABLED=false                 # true로 변경 시 실제 매매 실행
DAY_TRADING_ONLY=false                # true=당일 청산, false=스윙(오버나이트)
AUTONOMY_MODE=SEMI_AUTO               # SEMI_AUTO=승인 필요, AUTONOMOUS=자동 매매

# === 리스크 관리 ===
RISK_APPETITE=MODERATE                # CONSERVATIVE / MODERATE / AGGRESSIVE
MIN_CASH_RATIO=0.05                   # 최소 현금 비중 (5%)
MAX_DAILY_TRADES=30                   # 일일 최대 거래 횟수
```

> **계좌번호 참고:**
> - 10자리 전체 계좌번호를 넣으면 내부에서 CANO(앞 8자리) + 상품코드(뒤 2자리)로 자동 분리합니다.
> - 8자리 CANO만 넣을 경우 `KIS_PROD_TYPE` 2자리를 반드시 함께 설정해야 합니다.

> **LLM 참고:**
> - Codex 사용 시 `codex login`이 선행되어야 하며, Claude 사용 시 `claude` CLI가 PATH에 있어야 합니다.
> - Tier1은 호출 성격에 따라 기본값이 다릅니다: 스캔은 `low`, 분석은 `medium`.
> - Tier2 최종 검토는 기본적으로 `xhigh`를 사용합니다.

### 코인 (빗썸) 설정

코인 전용 환경변수는 `.env.example-coin`을 참조하세요:

```bash
# === 빗썸 API 인증 ===
BITHUMB_API_KEY=your_api_key
BITHUMB_API_SECRET=your_api_secret

# === 코인 운영 설정 ===
CRYPTO_ENABLED=true
CRYPTO_TRADING_ENABLED=true
CRYPTO_AUTONOMY_MODE=SEMI_AUTO
CRYPTO_SCAN_INTERVAL_HOURS=4          # 스캔 주기
CRYPTO_HOLDINGS_CHECK_INTERVAL_HOURS=2 # 보유 점검 주기
CRYPTO_WATCHLIST_SYMBOLS=BTC,ETH,XRP,SOL

# === 코인 리스크 관리 ===
CRYPTO_MAX_POSITION_PCT=20.0          # 종목당 최대 비중
CRYPTO_MIN_CASH_RATIO=0.10            # 최소 현금 비중 (10%)

# === 코인 전용 LLM (선택 — 미설정 시 주식과 동일) ===
# CRYPTO_LLM_PROVIDER=CLAUDE_CODE
# CRYPTO_LLM_MODEL_TIER1_SCAN=haiku
# CRYPTO_LLM_MODEL_TIER2=sonnet
```

> 전체 코인 설정 항목은 `.env.example-coin`을 참조하세요.

---

## 3. 실행

### Docker (권장)

```bash
docker compose up
# App: http://localhost:9000
# KIS MCP: http://localhost:3100
# 주식 대시보드: http://localhost:9000/admin
# 코인 대시보드: http://localhost:9000/admin-coin
```

### 로컬 개발

```bash
# KIS MCP 서버를 별도로 실행
docker compose up kis-mcp

# 다른 터미널에서
alembic upgrade head          # DB 마이그레이션
uvicorn main:app --reload     # http://localhost:8000
```

### 코인 전용 실행

```bash
./start-coin.sh               # 코인만 실행 (빗썸 preflight 포함)
./start-coin.sh -d             # 데몬 모드
```

---

## 4. 첫 실행 체크리스트

1. `TRADING_ENABLED=false` 상태에서 시작 (건조 실행)
2. `KIS_ACCOUNT_TYPE=VIRTUAL`로 모의 투자 먼저 테스트
3. Admin 대시보드(`/admin`)에서 에이전트 활동 모니터링
4. 수동 사이클 트리거로 동작 확인 후 `SCHEDULER_ENABLED=true`
5. 코인은 `CRYPTO_TRADING_ENABLED=false`로 먼저 스캔만 테스트
6. 로그(`logs/` 디렉토리)에서 AI 분석 결과와 주문 기록 확인

---

## 5. 테스트

```bash
pytest tests/ -v
pytest tests/api/test_health.py -v    # 단일 파일
```

인메모리 SQLite(`sqlite+aiosqlite://`)로 실행되며, 증권사 API 호출 없이 독립 테스트 가능합니다.

> **venv 참고:** 테스트는 항상 레포 루트에서 실행하세요. macOS/Linux는 `source .venv/bin/activate`, Windows는 `.venv\Scripts\activate`로 venv를 활성화한 후 진행합니다.
