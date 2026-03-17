# KIS Open API 레퍼런스 조회

KIS API 구현, 디버깅, 새 API 연동 시 로컬 레퍼런스를 참조한다.

## 사용자 인자

$ARGUMENTS — 검색할 API 기능 키워드 (예: 주문, 잔고조회, 시세, volume_rank, 체결 등)

## 로컬 레퍼런스

- 인덱스: `docs/kis-api/README.md` (전체 API 목록 + tr_id 매핑표)
- 국내주식 주문/계좌: `docs/kis-api/domestic/trading/*.md`
- 국내주식 시세: `docs/kis-api/domestic/quotations/*.md`
- 해외주식 주문/계좌: `docs/kis-api/overseas/trading/*.md`
- 해외주식 시세: `docs/kis-api/overseas/quotations/*.md`

## 도메인

| 구분 | URL |
|------|-----|
| 실전 | `https://openapi.koreainvestment.com:9443` |
| 모의 | `https://openapivts.koreainvestment.com:29443` |

## tr_id 규칙

- 주문/계좌 API: 실전 `T` / 모의 `V` 접두사
- 시세 API: 실전/모의 동일 tr_id

## 핵심 tr_id 매핑

| 기능 | 실전 | 모의 |
|------|------|------|
| 국내 매수 | TTTC0012U | VTTC0012U |
| 국내 매도 | TTTC0011U | VTTC0011U |
| 국내 정정/취소 | TTTC0013U | VTTC0013U |
| 국내 체결조회 (3개월이내) | TTTC0081R | VTTC0081R |
| 국내 체결조회 (3개월이전) | CTSC9215R | VTSC9215R |
| 국내 잔고 | TTTC8434R | VTTC8434R |
| 국내 매수가능 | TTTC8908R | VTTC8908R |
| 해외 체결조회 | TTTS3035R | VTTS3035R |
| 해외 잔고 | TTTS3012R | VTTS3012R |
| 해외 매수가능 | TTTS3007R | VTTS3007R |

## 프로젝트 내 KIS API 구현

| 파일 | 역할 |
|------|------|
| `trading/kis_api.py` | KIS REST API 직접 호출 (해외주식 주문/조회, 국내 체결조회) |
| `trading/mcp_client.py` | MCP 도구 호출 + 국내 직접 호출 래퍼 |
| `trading/account_manager.py` | 계좌/잔고 관리 상위 레이어 |

## 실행 절차

"$ARGUMENTS" 키워드에 대해:

### Step 1: 로컬 레퍼런스에서 검색
```bash
# 파일명으로 검색
find docs/kis-api -name "*.md" | xargs grep -li "$ARGUMENTS"

# 또는 Grep 도구로 내용 검색
```
`docs/kis-api/README.md` 인덱스 테이블에서 해당 API를 찾아 상세 문서를 읽는다.

### Step 2: 상세 문서 조회
해당 API의 마크다운 문서를 Read 도구로 읽어 tr_id, 파라미터, 주의사항을 확인한다.

### Step 3: 프로젝트 코드와 대조
`trading/kis_api.py`, `trading/mcp_client.py`에서 해당 API 사용처를 확인하고 일치 여부를 검증한다.

### Step 4: (필요시) 공식 레포 최신 확인
로컬 문서로 부족할 경우에만 GitHub 레포를 직접 조회:
```bash
gh api repos/koreainvestment/open-trading-api/contents/examples_llm/{카테고리}/{api_name}/{api_name}.py --jq '.content' | base64 -d
```
