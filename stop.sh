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
PID_FILES=(
    "$APP_DIR/.momo.pid"
    "$APP_DIR/.momo.virtual.pid"
    "$APP_DIR/.momo.real.pid"
    "$APP_DIR/.momo-stock.pid"
    "$APP_DIR/.momo-stock.virtual.pid"
    "$APP_DIR/.momo-stock.real.pid"
)

wait_for_exit() {
    local pid="$1"
    local attempts="${2:-5}"
    local i

    for ((i=0; i<attempts; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

stop_process_tree() {
    local pid="$1"
    local children

    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    children="$(pgrep -P "$pid" || true)"
    kill -TERM "$pid" 2>/dev/null || true
    if [ -n "$children" ]; then
        kill -TERM $children 2>/dev/null || true
    fi

    if wait_for_exit "$pid" 5; then
        return 0
    fi

    if [ -n "$children" ]; then
        kill -KILL $children 2>/dev/null || true
    fi
    kill -KILL "$pid" 2>/dev/null || true
    wait_for_exit "$pid" 2 || true
}

stop_process_group() {
    local pid="$1"
    local pgid

    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [ -z "$pgid" ]; then
        stop_process_tree "$pid"
        return 0
    fi
    if [ "$pgid" != "$pid" ]; then
        stop_process_tree "$pid"
        return 0
    fi

    kill -TERM -- "-$pgid" 2>/dev/null || true
    if wait_for_exit "$pid" 5; then
        return 0
    fi

    kill -KILL -- "-$pgid" 2>/dev/null || true
    wait_for_exit "$pid" 2 || true
}

stop_server() {
    local stopped=false
    local pid
    local parent_pid
    local pid_file

    # PID 파일 기반 종료
    for pid_file in "${PID_FILES[@]}"; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                echo "🛑 momo-trading 서버 종료 (PID: $pid)"
                stop_process_group "$pid"
                stopped=true
            fi
            rm -f "$pid_file"
        fi
    done

    # uvicorn 프로세스 직접 종료 (포그라운드 실행 대응)
    for parent_pid in $(pgrep -f "uvicorn main:app" || true); do
        echo "🛑 uvicorn 프로세스 종료 (PID: $parent_pid)"
        stop_process_tree "$parent_pid"
        stopped=true
    done

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
