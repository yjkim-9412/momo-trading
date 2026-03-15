---
name: js-convention
description: Use when creating, editing, or refactoring any .js file under admin/static/js/ (shared/, app/, coin/). Triggers on import/export changes, new ES module files, DOM rendering, SSE handlers, state management, Feed/Monitor/Toast usage, and admin dashboard frontend work.
---

# Admin JS 코딩 컨벤션

## 아키텍처

```
admin/static/js/
  shared/          ← 순수 유틸, 토스트, 모니터, 피드 (양쪽 대시보드 공유)
  app/             ← 주식(KRX/US) 대시보드 전용
  coin/            ← 코인(CRYPTO) 대시보드 전용
```

- **shared/** 는 app/과 coin/ 양쪽에서 import됨. app↔coin 간 직접 import 금지.
- **진입점**: `app/app-main.js`, `coin/coin-main.js` — HTML에서 `<script type="module">` 하나로 로드.
- **빌드 시스템 없음** — 네이티브 ES 모듈, 브라우저 직접 로딩.

## ES 모듈 규칙

### import 순서
```javascript
// 1. shared 모듈
import { escapeHtml, fetchJSON } from '../shared/admin-core.js';
import * as Toast from '../shared/admin-toast.js';
import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';

// 2. 같은 레이어 sibling 모듈 (state 먼저)
import { state, API, currentScope } from './app-state.js';
import { loadBalance } from './app-account.js';
```

### export 패턴
```javascript
// named export만 사용 (default export 금지)
export function loadBalance() { ... }
export const API = '/api/v1/admin';
```

### 순환 import 방지
- **상태는 `*-state.js`에 집중**. 다른 모듈은 state만 import.
- 모듈 A→B, B→A 순환이 필요하면 **late-bound callback** 사용:
  ```javascript
  // state.js
  export const state = { _loadBalance: null };

  // main.js (진입점에서 연결)
  import { loadBalance } from './account.js';
  state._loadBalance = loadBalance;

  // sse.js (state 경유 호출)
  if (state._loadBalance) state._loadBalance();
  ```

## 상태 관리

### mutable state 객체 패턴
```javascript
// *-state.js
export const state = {
  currentView: 'live',
  currentMarket: 'KRX',
  autoScroll: true,
};

// 다른 모듈에서 직접 프로퍼티 수정 (object export는 참조)
import { state } from './app-state.js';
state.currentView = 'report';
```

### Monitor/Feed 설정 주입
```javascript
Monitor.init({
  getState: () => state.monitorState[currentScope()],
  scopeLabel: () => currentScope() === 'KRX' ? '국내' : '해외',
});
```

## DOM 보안

- **동적 텍스트**는 반드시 `escapeHtml()` 또는 `textContent`/`createElement` 사용.
- `innerHTML`/`insertAdjacentHTML`은 **정적 마크업 + escaped 값**만 허용.
- DOM API(`createElement`+`textContent`) 우선, `insertAdjacentHTML`은 복잡한 구조에서만.

### inline onclick에서 모듈 함수 호출
- `window.__feedToggleDetail = toggleDetail;` 처럼 글로벌 브릿지 등록.
- HTML 파일의 `onclick` 핸들러용 함수는 `*-main.js`에서 `window.fn = fn;` 으로 노출.

## 네이밍 컨벤션

| 대상 | 규칙 | 예시 |
|------|------|------|
| 파일명 | `kebab-case` | `admin-core.js`, `coin-account.js` |
| export 함수 | `camelCase` | `loadBalance()`, `renderHoldings()` |
| export 상수 | `UPPER_SNAKE_CASE` | `PIPELINE_STEPS`, `API` |
| 비공개 함수 | `_` prefix | `_appendFlexRow()` |
| state 내부 callback | `_` prefix | `state._loadBalance` |

## 파일 역할

| 파일 | 역할 | 제한 |
|------|------|------|
| `*-state.js` | 상태 + Monitor/Feed init + 순수 헬퍼 | DOM 접근 금지 |
| `*-sse.js` | SSE 연결 + 이벤트 라우팅 | state 경유 외부 함수 호출 |
| `*-main.js` | 진입점 + DOMContentLoaded + window.* | 항상 마지막 로드 |

## 주의사항

- `var` 사용 OK (기존 코드와 일관성). 신규 코드는 `let`/`const` 권장.
- `lucide.createIcons()` — 동적 DOM 삽입 후 `refreshIcons()` 호출 필수.
- 코인과 주식 대시보드는 **완전 격리** — SSE, API prefix, 테마, 상태 모두 독립.
- `Admin.*` 네임스페이스는 **폐기됨**. ES import/export만 사용.
