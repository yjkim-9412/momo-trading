#!/usr/bin/env bash
#
# momo-trading 코인 전용 실행 스크립트
# KIS MCP Docker 없이 빗썸 코인 거래만 실행
#
# 사용법:
#   ./start-coin.sh          — 포그라운드 실행
#   ./start-coin.sh -d       — 백그라운드(데몬) 실행
#   ./start-coin.sh stop     — 서버 종료
#   ./start-coin.sh status   — 실행 상태 확인
#   ./start-coin.sh logs     — 실시간 로그 보기
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
PID_FILE="$APP_DIR/.momo-coin.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$APP_DIR/logs/momo-coin.log"
DATA_DIR="$APP_DIR/data"
HOST="${MOMO_HOST:-0.0.0.0}"
PORT="${MOMO_PORT:-9000}"

# 코인 전용: 주식 시장 비활성화, MCP/WebSocket 연결 건너뜀
export ENABLED_MARKETS="CRYPTO"

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
        echo "⚠️  .env 파일이 없습니다. .env.example-coin을 참고하세요."
    fi
}

check_crypto_env() {
    local crypto_enabled
    crypto_enabled=$(grep -E '^CRYPTO_ENABLED=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')

    if [ "$crypto_enabled" != "true" ]; then
        echo "❌ CRYPTO_ENABLED=true가 .env에 설정되지 않았습니다."
        echo "   .env.example-coin을 참고하여 코인 설정을 추가하세요."
        exit 1
    fi

    local api_key
    api_key=$(grep -E '^BITHUMB_API_KEY=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')

    if [ -z "$api_key" ]; then
        echo "⚠️  BITHUMB_API_KEY가 비어있습니다."
        echo "   공개 시세 조회는 가능하지만 잔고/주문은 동작하지 않습니다."
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

check_port() {
    if lsof -ti:"$PORT" >/dev/null 2>&1; then
        echo "❌ 포트 $PORT 이미 사용 중"
        echo "   ./start-coin.sh stop 으로 기존 프로세스를 종료하세요."
        exit 1
    fi
}

print_banner() {
    local mode="$1"
    echo ""
    echo "🪙 momo-trading 코인 전용 시작 ($mode)"
    echo "   Host:      $HOST:$PORT"
    echo "   Admin:     http://localhost:$PORT/admin"
    echo "   Coin:      http://localhost:$PORT/admin-coin"
    echo "   Docker:    불필요 (빗썸 REST API 직접 연결)"
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
            echo "🛑 코인 서버 종료 (PID: $pid)..."
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
        echo "ℹ️  실행 중인 코인 서버 없음"
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
        echo "=== 코인 서버 ==="
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "✅ 코인 서버 실행 중 (PID: $PID)"
                echo "   Coin: http://localhost:$PORT/admin-coin"
            else
                echo "❌ 프로세스 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        elif pgrep -f "uvicorn main:app" >/dev/null 2>&1; then
            echo "✅ 서버 실행 중 (포그라운드)"
        else
            echo "❌ 실행 중인 코인 서버 없음"
        fi
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
        check_crypto_env
        check_port
        migrate_db

        print_banner "백그라운드"
        echo "   Log:      $LOG_FILE"

        launch_daemon
        echo "   PID:      $(cat "$PID_FILE")"
        echo ""
        echo "종료: ./start-coin.sh stop"
        ;;

    ""|--foreground)
        activate_venv
        check_env
        check_crypto_env
        check_port
        migrate_db

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
