# Crypto (Bithumb) Domain Architecture

> **개발자 대상 코드 레벨 아키텍처 문서입니다.** 일반 개요는 [README](../README.md), 사용자 관점 상세는 [코인 시스템 상세](crypto-system.md)를 참조하세요.

## 1. 도메인 구조

```mermaid
graph TB
    subgraph "Interface Layer (Protocol)"
        BC[BrokerClient Protocol]
        MDP[MarketDataProvider Protocol]
        RP[RealtimeProvider Protocol]
        MSP[MarketScannerProtocol]
    end

    subgraph "Stock Implementation (KIS)"
        MCPClient[MCPClient<br/>trading/mcp_client.py]
        KISAPI[KIS REST API<br/>trading/kis_api.py]
        KISWS[KIS WebSocket<br/>trading/kis_websocket.py]
        StockScanner[MarketScanner<br/>agent/market_scanner.py]
    end

    subgraph "Crypto Implementation (Bithumb)"
        BTHClient[BithumbClient<br/>trading/bithumb_client.py]
        BTHWS[BithumbWebSocket<br/>trading/bithumb_websocket.py]
        CryptoScanner[CryptoScanner<br/>agent/crypto_scanner.py]
    end

    BC --> MCPClient
    BC --> BTHClient
    MDP --> MCPClient
    MDP --> BTHClient
    RP --> KISWS
    RP --> BTHWS
    MSP --> StockScanner
    MSP --> CryptoScanner

    MCPClient --> KISAPI

    style BC fill:#6366f1,color:#fff
    style MDP fill:#6366f1,color:#fff
    style RP fill:#6366f1,color:#fff
    style MSP fill:#6366f1,color:#fff
    style BTHClient fill:#f59e0b,color:#000
    style BTHWS fill:#f59e0b,color:#000
    style CryptoScanner fill:#f59e0b,color:#000
```

## 2. Market Scope 분리 구조

```mermaid
graph LR
    subgraph "Market Scopes"
        KRX["KRX<br/>한국 주식<br/>KOSPI, KOSDAQ"]
        US["US<br/>미국 주식<br/>NASDAQ, NYSE, AMEX"]
        CRYPTO["CRYPTO<br/>암호화폐<br/>BITHUMB"]
    end

    subgraph "Shared Pipeline"
        TA[TradingAgent]
        CA[ChartAnalyzer<br/>기술지표 RSI/MACD/BB]
        LLM[LLM Factory<br/>Tier1 + Tier2]
        RM[RiskManager]
        PM[PerformanceTracker]
    end

    subgraph "Isolated Config"
        KC[KIS Config<br/>TRADING_ENABLED<br/>AUTONOMY_MODE<br/>MAX_DAILY_TRADES]
        CC[Crypto Config<br/>CRYPTO_TRADING_ENABLED<br/>CRYPTO_AUTONOMY_MODE<br/>CRYPTO_MAX_DAILY_TRADES]
    end

    KRX --> TA
    US --> TA
    CRYPTO --> TA
    TA --> CA
    TA --> LLM
    TA --> RM
    TA --> PM

    KRX -.-> KC
    US -.-> KC
    CRYPTO -.-> CC

    style KRX fill:#ef4444,color:#fff
    style US fill:#3b82f6,color:#fff
    style CRYPTO fill:#f59e0b,color:#000
    style CC fill:#f59e0b,color:#000
```

## 3. 코인 거래 플로우

```mermaid
sequenceDiagram
    participant SCH as Scheduler<br/>(4h 주기 cron)
    participant TA as TradingAgent<br/>_cycle_mixin
    participant CS as CryptoScanner
    participant BTH as BithumbClient
    participant LLM as LLM (Tier1/Tier2)
    participant RM as RiskManager
    participant DB as Database

    Note over SCH: 24/7 운영 - 4시간 간격

    SCH->>TA: run_cycle(market="BITHUMB")
    TA->>TA: is_crypto_market() → true

    rect rgb(255, 248, 220)
        Note over CS,BTH: Phase 1: 시장 스캔
        TA->>CS: scan(market="BITHUMB")
        CS->>BTH: get_market_overview()
        BTH-->>CS: ALL_KRW 전체 코인 티커
        CS->>CS: 24h 거래대금 정렬 + 급등/급락
        CS->>CS: CRYPTO_WATCHLIST_SYMBOLS 병합
        CS->>LLM: Tier1 스캔 프롬프트
        LLM-->>CS: 5~8개 후보 선별
        CS-->>TA: selected_coins[]
    end

    rect rgb(220, 237, 255)
        Note over TA,LLM: Phase 2: 종목별 분석
        loop 각 선별 코인
            TA->>BTH: get_current_price(symbol)
            TA->>BTH: get_daily_price(symbol, "24h")
            TA->>BTH: get_minute_price(symbol, 5min)
            TA->>TA: ChartAnalyzer → RSI, MACD, BB 등
            TA->>LLM: Tier1 분석 프롬프트
            LLM-->>TA: BUY/HOLD + confidence

            alt confidence >= 80% (fast path)
                TA->>TA: skip Tier2
            else Tier2 필요
                TA->>LLM: Tier2 최종 검토
                LLM-->>TA: approved + quantity
            end
        end
    end

    rect rgb(255, 220, 220)
        Note over TA,DB: Phase 3: 리스크 + 실행
        TA->>RM: check(signal, crypto_limits)
        RM->>RM: CRYPTO R:R floor 적용
        RM-->>TA: approved / adjusted_qty

        alt SEMI_AUTO 모드
            TA->>DB: 추천 저장 (PENDING)
            Note over DB: 사용자 승인 대기<br/>/admin-coin에서 승인/거절
        else AUTONOMOUS 모드
            TA->>BTH: place_order(symbol, BUY, qty, price)
            BTH-->>TA: order_result
            TA->>DB: BrokerOrder 저장
        end
    end
```

## 4. 코인 Admin 페이지 구조

```mermaid
graph TB
    subgraph ADMIN_STOCK["admin — 주식"]
        SA["index.html<br/>주식 대시보드"]
        SAP["api/v1/admin/*<br/>KRX/US 데이터"]
        SSE1["SSE: admin/stream"]
    end

    subgraph ADMIN_COIN["admin-coin — 코인"]
        CA["coin.html<br/>코인 대시보드"]
        CAP["api/v1/coin/*<br/>BITHUMB 데이터"]
        SSE2["SSE: coin/stream"]
    end

    subgraph "Shared Backend"
        AM[AccountManager]
        AL[ActivityLogger]
        AG[TradingAgent]
    end

    SA --> SAP
    SA --> SSE1
    CA --> CAP
    CA --> SSE2

    SAP --> AM
    CAP --> AM
    SAP --> AL
    CAP --> AL
    SAP --> AG
    CAP --> AG

    style SA fill:#ef4444,color:#fff
    style CA fill:#f59e0b,color:#000
    style SAP fill:#ef4444,color:#fff
    style CAP fill:#f59e0b,color:#000
```

## 5. 코인 스케줄 타임라인

```mermaid
gantt
    title 코인 24/7 스케줄 (KST)
    dateFormat HH:mm

    section 주기 스캔
    스캔 사이클 1    :crit, 00:00, 30min
    스캔 사이클 2    :crit, 04:00, 30min
    스캔 사이클 3    :crit, 08:00, 30min
    스캔 사이클 4    :crit, 12:00, 30min
    스캔 사이클 5    :crit, 16:00, 30min
    스캔 사이클 6    :crit, 20:00, 30min

    section 보유 점검
    점검 1           :active, 01:00, 15min
    점검 2           :active, 03:00, 15min
    점검 3           :active, 05:00, 15min
    점검 4           :active, 07:00, 15min
    점검 5           :active, 09:00, 15min
    점검 6           :active, 11:00, 15min

    section 일일 리뷰
    24h 성과 리뷰    :done, 00:00, 30min
```

## 6. 환경변수 분리 구조

```mermaid
graph TB
    subgraph "주식 (KIS) 설정"
        direction TB
        KA[KIS_APP_KEY / KIS_APP_SECRET]
        KT[TRADING_ENABLED]
        KM[AUTONOMY_MODE]
        KR[MAX_SINGLE_ORDER_KRW<br/>MIN_CASH_RATIO<br/>MAX_DAILY_TRADES]
    end

    subgraph "코인 (Bithumb) 설정"
        direction TB
        BA[BITHUMB_API_KEY / BITHUMB_API_SECRET]
        CT[CRYPTO_TRADING_ENABLED]
        CM[CRYPTO_AUTONOMY_MODE]
        CR[CRYPTO_MAX_SINGLE_ORDER_KRW<br/>CRYPTO_MIN_CASH_RATIO<br/>CRYPTO_MAX_DAILY_TRADES]
    end

    subgraph "공통 설정"
        direction TB
        LLM[LLM_PROVIDER<br/>CLAUDE_CODE_MODEL]
        DB[DATABASE_URL]
        SC[SCHEDULER_ENABLED]
    end

    KA -.->|독립| BA
    KT -.->|독립| CT
    KM -.->|독립| CM
    KR -.->|독립| CR

    style KA fill:#ef4444,color:#fff
    style KT fill:#ef4444,color:#fff
    style KM fill:#ef4444,color:#fff
    style KR fill:#ef4444,color:#fff
    style BA fill:#f59e0b,color:#000
    style CT fill:#f59e0b,color:#000
    style CM fill:#f59e0b,color:#000
    style CR fill:#f59e0b,color:#000
    style LLM fill:#6366f1,color:#fff
    style DB fill:#6366f1,color:#fff
    style SC fill:#6366f1,color:#fff
```
