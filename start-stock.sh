#!/usr/bin/env bash
#
# momo-trading 주식 전용 실행 스크립트
# 코인(빗썸) 비활성화, KIS MCP Docker + 주식 거래만 실행
#
# 사용법:
#   ./start-stock.sh          — 포그라운드 실행 (Docker + 서버)
#   ./start-stock.sh -d       — 백그라운드(데몬) 실행
#   ./start-stock.sh stop     — 서버 + Docker 종료
#   ./start-stock.sh status   — 실행 상태 확인
#   ./start-stock.sh logs     — 실시간 로그 보기
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
PID_FILE="$APP_DIR/.momo-stock.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$APP_DIR/logs/momo-stock.log"
DATA_DIR="$APP_DIR/data"
HOST="${MOMO_HOST:-0.0.0.0}"
PORT="${MOMO_PORT:-9000}"
DOCKER_STATUS_MSG=""

# 주식 전용: 코인 시스템 비활성화 (.env에 CRYPTO_ENABLED=true여도 오버라이드)
export CRYPTO_ENABLED=false

# ── 공통 함수 ──

activate_venv() {
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
    else
        echo "❌ venv 없음: $VENV_DIR"
        echo "   python -m venv venv && pip install -r requirements.txt"
        exit 1
    fi
}

check_env() {
    if [ ! -f "$APP_DIR/.env" ]; then
        echo "⚠️  .env 파일이 없습니다. .env.example을 참고하세요."
    fi
}

check_kis_env() {
    local app_key paper_key
    app_key=$(grep -E '^KIS_APP_KEY=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    paper_key=$(grep -E '^KIS_PAPER_APP_KEY=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')

    if [ -z "$app_key" ] && [ -z "$paper_key" ]; then
        echo "⚠️  KIS API 키가 모두 비어있습니다."
        echo "   KIS_APP_KEY 또는 KIS_PAPER_APP_KEY를 .env에 설정하세요."
    fi
}

ensure_runtime_dirs() {
    mkdir -p "$LOG_DIR" "$DATA_DIR"
}

migrate_db() {
    echo "🗃️  DB 마이그레이션 확인..."
    if ! python -m alembic upgrade head; then
        echo "❌ Alembic migration 실패"
        exit 1
    fi
}

docker_daemon_ready() {
    local err
    local code

    if ! command -v docker >/dev/null 2>&1; then
        DOCKER_STATUS_MSG="Docker CLI가 설치되어 있지 않습니다."
        return 1
    fi

    set +e
    err=$(docker info 2>&1 >/dev/null)
    code=$?
    set -e
    if [ "$code" -ne 0 ]; then
        DOCKER_STATUS_MSG="${err%%$'\n'*}"
        return 1
    fi

    DOCKER_STATUS_MSG=""
    return 0
}

ensure_docker() {
    if ! docker_daemon_ready; then
        echo "⚠️  Docker daemon 접근 불가: $DOCKER_STATUS_MSG"
        echo "   KIS MCP 서버 없이 실행합니다."
        return 1
    fi

    cd "$APP_DIR"
    if docker compose ps --status running -q kis-mcp 2>/dev/null | grep -q .; then
        echo "🐳 KIS MCP 서버 이미 실행 중"
    else
        echo "🐳 KIS MCP 서버 시작..."
        docker compose up kis-mcp -d

        echo -n "   대기 중"
        for i in $(seq 1 15); do
            if docker compose ps --status running -q kis-mcp 2>/dev/null | grep -q .; then
                echo " ✅"
                break
            fi
            echo -n "."
            sleep 2
        done
    fi
    return 0
}

print_docker_status() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "ℹ️  Docker 미설치"
        return
    fi

    if ! docker_daemon_ready; then
        echo "⚠️  Docker daemon 접근 불가: $DOCKER_STATUS_MSG"
        return
    fi

    docker compose ps 2>/dev/null || echo "❌ Docker 컨테이너 없음"
}

check_port() {
    if lsof -ti:"$PORT" >/dev/null 2>&1; then
        echo "❌ 포트 $PORT 이미 사용 중"
        echo "   ./start-stock.sh stop 으로 기존 프로세스를 종료하세요."
        exit 1
    fi
}

print_banner() {
    local mode="$1"
    echo ""
    echo "📈 momo-trading 주식 전용 시작 ($mode)"
    echo "   Host:      $HOST:$PORT"
    echo "   Admin:     http://localhost:$PORT/admin"
    echo "   Crypto:    비활성화"
}

launch_daemon() {
    : > "$LOG_FILE"

    if command -v setsid >/dev/null 2>&1; then
        setsid env PYTHONUNBUFFERED=1 \
            python -u -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            >> "$LOG_FILE" 2>&1 < /dev/null &
    else
        nohup env PYTHONUNBUFFERED=1 \
            python -u -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            >> "$LOG_FILE" 2>&1 < /dev/null &
    fi

    echo $! > "$PID_FILE"
    sleep 2

    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "❌ 서버 기동 실패"
        echo "   최근 로그:"
        sed -n '1,200p' "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

do_stop() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "🛑 주식 서버 종료 (PID: $pid)..."
            kill "$pid"
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            echo "   ✅ 종료 완료"
        else
            echo "ℹ️  프로세스 이미 종료됨 (stale PID: $pid)"
        fi
        rm -f "$PID_FILE"
    else
        echo "ℹ️  실행 중인 주식 서버 없음"
    fi

    # Docker MCP 서버도 종료
    if docker_daemon_ready 2>/dev/null; then
        if docker compose ps --status running -q kis-mcp 2>/dev/null | grep -q .; then
            echo "🐳 KIS MCP 서버 종료..."
            docker compose stop kis-mcp
        fi
    fi
}

# ── 명령 분기 ──

cd "$APP_DIR"
ensure_runtime_dirs

case "${1:-}" in
    stop)
        do_stop
        ;;

    status)
        echo "=== 주식 서버 ==="
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "✅ 주식 서버 실행 중 (PID: $PID)"
                echo "   Admin: http://localhost:$PORT/admin"
            else
                echo "❌ 프로세스 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        elif pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
            echo "✅ 서버 실행 중 (포그라운드)"
        else
            echo "❌ 실행 중인 주식 서버 없음"
        fi

        echo ""
        echo "=== Docker ==="
        print_docker_status
        ;;

    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "로그 파일 없음: $LOG_FILE"
            echo "백그라운드(-d) 모드로 실행해야 로그 파일이 생성됩니다."
        fi
        ;;

    -d|--daemon)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "이미 실행 중 (PID: $(cat "$PID_FILE"))"
            exit 1
        fi

        activate_venv
        check_env
        check_kis_env
        check_port
        migrate_db
        ensure_docker || true

        print_banner "백그라운드"
        echo "   Log:      $LOG_FILE"

        launch_daemon
        echo "   PID:      $(cat "$PID_FILE")"
        echo ""
        echo "종료: ./start-stock.sh stop"
        ;;

    ""|--foreground)
        activate_venv
        check_env
        check_kis_env
        check_port
        migrate_db
        ensure_docker || true

        print_banner "포그라운드"
        echo "   종료: Ctrl+C"
        echo ""

        PYTHONUNBUFFERED=1 python -u -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info
        ;;

    *)
        echo "사용법: $0 [-d|stop|status|logs]"
        exit 1
        ;;
esac
