#!/usr/bin/env bash
#
# WSL 전용 — momo-trading 주식 서버 실행 래퍼
#
# WSL 기본 셸이 root이므로 Claude Code CLI가 동작하지 않음.
# 이 스크립트가 momo 사용자로 전환 후 start-stock.sh를 실행한다.
#
# 사용법:
#   bash wsl-start-stock.sh          — 포그라운드 실행 (Ctrl+C로 종료)
#   bash wsl-start-stock.sh -d       — 백그라운드 실행
#   bash wsl-start-stock.sh stop     — 종료
#   bash wsl-start-stock.sh status   — 상태 확인
#   bash wsl-start-stock.sh logs     — 로그 보기
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${MOMO_USER:-momo}"

# momo 사용자 존재 확인
if ! id "$RUN_USER" >/dev/null 2>&1; then
    echo "❌ $RUN_USER 사용자가 없습니다."
    echo "   생성: sudo useradd -m -s /bin/bash -G docker $RUN_USER"
    exit 1
fi

# 인자를 안전하게 전달하기 위해 배열로 구성
ARGS=("$@")
ESCAPED_ARGS=""
for arg in "${ARGS[@]+"${ARGS[@]}"}"; do
    ESCAPED_ARGS="$ESCAPED_ARGS '$arg'"
done

# root → momo 전환, 이미 momo면 직접 실행
if [ "$(id -u)" -eq 0 ]; then
    echo "🔄 $RUN_USER 사용자로 전환 (Claude Code는 root 차단)"
    # Ctrl+C(SIGINT)를 자식에게 전달
    trap 'kill -INT -$PID 2>/dev/null; wait $PID 2>/dev/null' INT TERM
    su -s /bin/bash "$RUN_USER" -c "cd '$APP_DIR' && bash '$APP_DIR/start-stock.sh' $ESCAPED_ARGS" &
    PID=$!
    wait $PID 2>/dev/null
    exit $?
elif [ "$(whoami)" = "$RUN_USER" ]; then
    exec bash "$APP_DIR/start-stock.sh" "$@"
else
    echo "⚠️  현재 사용자: $(whoami) — $RUN_USER로 전환할 수 없습니다."
    echo "   root 또는 $RUN_USER 사용자로 실행하세요."
    exit 1
fi
