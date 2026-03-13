#!/usr/bin/env bash
#
# momo-trading 전체 종료 스크립트 (서버 + Docker)
#
# 사용법:
#   ./stop.sh         — 서버 + Docker 컨테이너 모두 종료
#   ./stop.sh server  — 서버만 종료
#   ./stop.sh docker  — Docker 컨테이너만 종료
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.momo.pid"

stop_server() {
    local stopped=false

    # PID 파일 기반 종료
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🛑 momo-trading 서버 종료 (PID: $PID)"
            kill "$PID"
            stopped=true
        fi
        rm -f "$PID_FILE"
    fi

    # uvicorn 프로세스 직접 종료 (포그라운드 실행 대응)
    if pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
        echo "🛑 uvicorn 프로세스 종료"
        pkill -f "uvicorn main:app" 2>/dev/null || true
        stopped=true
    fi

    if [ "$stopped" = false ]; then
        echo "ℹ️  실행 중인 서버 없음"
    fi
}

stop_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "ℹ️  Docker가 설치되어 있지 않음"
        return
    fi

    cd "$APP_DIR"
    if docker compose ps --status running -q 2>/dev/null | grep -q .; then
        echo "🐳 Docker 컨테이너 종료"
        docker compose down
    else
        echo "ℹ️  실행 중인 컨테이너 없음"
    fi
}

case "${1:-all}" in
    server)
        stop_server
        ;;
    docker)
        stop_docker
        ;;
    all|"")
        stop_server
        stop_docker
        echo ""
        echo "✅ 전체 종료 완료"
        ;;
    *)
        echo "사용법: $0 [server|docker|all]"
        exit 1
        ;;
esac
