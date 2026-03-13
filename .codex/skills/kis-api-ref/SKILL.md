---
name: kis-api-ref
description: 한국투자증권(KIS) Open API 공식 샘플코드 레포(koreainvestment/open-trading-api)를 gh CLI로 검색·조회하여 API URL, tr_id, 파라미터, 호출 패턴을 확인한다. KIS API 구현, 디버깅, 새 API 연동 시 사용.
---

# KIS Open API 레퍼런스 조회

증권사 API 참조가 필요할 때 `gh` CLI로 한국투자증권 공식 샘플코드 레포를 조회한다.

**레포:** `koreainvestment/open-trading-api`

## 레포 구조

```
koreainvestment/open-trading-api
├── examples_llm/              # LLM용 기능 단위 샘플 (1 API = 1 폴더)
│   ├── kis_auth.py             # 인증 공통 함수 (_url_fetch, 토큰 관리)
│   ├── auth/                   # 토큰(auth_token) · 웹소켓 접속키(auth_ws_token)
│   ├── domestic_stock/         # 국내주식 — 100+개 API
│   ├── overseas_stock/         # 해외주식 (NASDAQ, NYSE, AMEX, 홍콩, 일본 등)
│   ├── domestic_bond/          # 국내채권
│   ├── domestic_futureoption/  # 국내선물옵션
│   ├── overseas_futureoption/  # 해외선물옵션
│   ├── etfetn/                 # ETF/ETN
│   └── elw/                    # ELW
├── examples_user/              # 사용자용 통합 예제 (상품별 함수 모음)
├── docs/convention.md          # 코딩 컨벤션 가이드
├── strategy_builder/           # 전략 설계 UI
├── backtester/                 # 백테스팅 엔진
├── stocks_info/                # 종목정보 마스터 파일
├── MCP/                        # MCP 서버 연동 가이드
└── kis_devlp.yaml              # API 설정 템플릿 (앱키, 도메인, 계좌번호)
```

## 주요 API 카테고리

| 카테고리 | 폴더 경로 | 핵심 API |
|---------|----------|---------|
| 국내주식 주문/계좌 | `examples_llm/domestic_stock/` | `order_cash`(현금주문), `inquire_balance`(잔고), `inquire_daily_ccld`(일별체결), `inquire_psbl_order`(주문가능) |
| 국내주식 시세 | `examples_llm/domestic_stock/` | `inquire_price`(현재가), `inquire_daily_price`(일별시세), `volume_rank`(거래량순위), `fluctuation`(등락률) |
| 해외주식 | `examples_llm/overseas_stock/` | `order`(주문), `inquire_balance`(잔고), `price`(시세), `inquire_ccnl`(체결) |
| 인증 | `examples_llm/auth/` | `auth_token`(REST 토큰), `auth_ws_token`(WebSocket 접속키) |

## API 파일 패턴

- `{api_name}/{api_name}.py` — 한줄호출 함수 (API_URL, tr_id, 파라미터, docstring)
- `{api_name}/chk_{api_name}.py` — 테스트/검증 파일

## 엔드포인트 & tr_id 패턴

- 국내주식 주문: `/uapi/domestic-stock/v1/trading/{기능}`
- 국내주식 시세: `/uapi/domestic-stock/v1/quotations/{기능}`
- 해외주식: `/uapi/overseas-stock/v1/trading/{기능}`
- 실전 도메인: `https://openapi.koreainvestment.com:9443`
- 모의 도메인: `https://openapivts.koreainvestment.com:29443`
- tr_id 규칙: `T`(실전) / `V`(모의) 접두사

| 기능 | 실전 tr_id | 모의 tr_id |
|------|-----------|-----------|
| 국내 매수 | TTTC0012U | VTTC0012U |
| 국내 매도 | TTTC0011U | VTTC0011U |
| 국내 잔고 | TTTC8434R | VTTC8434R |

- 해외 거래소코드: `NASD`(나스닥), `NYSE`(뉴욕), `AMEX`, `SEHK`(홍콩), `TKSE`(일본), `HASE`(베트남 하노이), `VNSE`(베트남 호치민)

## 조회 절차

### Step 1: 키워드로 코드 검색
```bash
gh search code --repo koreainvestment/open-trading-api "{검색어}" --limit 10
```

### Step 2: 관련 폴더 탐색
```bash
gh api repos/koreainvestment/open-trading-api/contents/examples_llm/{카테고리}/{api_name} --jq '.[] | "\(.type) \(.name)"'
```

### Step 3: 샘플코드 조회
```bash
gh api repos/koreainvestment/open-trading-api/contents/{파일경로} --jq '.content' | base64 -d
```

### Step 4: 결과 정리
- API URL, tr_id, 필수 파라미터 정리
- 요청/응답 구조 설명
- 현재 프로젝트(MOMO Trading) 코드와의 연관성 안내
