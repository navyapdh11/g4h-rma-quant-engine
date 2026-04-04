#!/bin/bash
# G4H-RMA Watcher Daemon — Auto-restart on crash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENGINE_PID=""
RESTART_COUNT=0
MAX_RESTARTS=100
HEALTH_CHECK_INTERVAL=30
RESTART_DELAY=5
LOG_FILE="$SCRIPT_DIR/logs/watcher.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

start_engine() {
    source "$SCRIPT_DIR/venv/bin/activate"

    # Rotate logs if needed
    if [ -f "$SCRIPT_DIR/logs/g4h-quant-engine.log" ]; then
        size=$(stat -c%s "$SCRIPT_DIR/logs/g4h-quant-engine.log" 2>/dev/null || echo 0)
        if [ "$size" -gt 10485760 ]; then
            mv "$SCRIPT_DIR/logs/g4h-quant-engine.log" "$SCRIPT_DIR/logs/g4h-quant-engine.log.old"
        fi
    fi

    nohup python main.py >> "$SCRIPT_DIR/logs/g4h-quant-engine.log" 2>&1 &
    ENGINE_PID=$!
    RESTART_COUNT=$((RESTART_COUNT + 1))

    log "START Engine launched (PID: $ENGINE_PID, restart #${RESTART_COUNT})"
    echo "$ENGINE_PID" > "$SCRIPT_DIR/.engine.pid"
}

check_health() {
    if [ -z "$ENGINE_PID" ]; then
        return 1
    fi

    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
        return 1
    fi

    # HTTP health check
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null)
    if [ "$response" != "200" ]; then
        return 1
    fi

    return 0
}

# Cleanup on exit
cleanup() {
    log "STOP Watcher shutting down"
    if [ -n "$ENGINE_PID" ] && kill -0 "$ENGINE_PID" 2>/dev/null; then
        kill "$ENGINE_PID" 2>/dev/null
    fi
    rm -f "$SCRIPT_DIR/.watcher.pid"
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP EXIT

log "START Watcher daemon initialized"

# Initial start
start_engine

# Main loop
while true; do
    sleep "$HEALTH_CHECK_INTERVAL"

    if ! check_health; then
        if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]; then
            log "CRITICAL Max restarts reached ($MAX_RESTARTS). Stopping."
            break
        fi

        log "CRASH Engine down (PID: $ENGINE_PID). Restarting in ${RESTART_DELAY}s..."

        # Kill old process if still alive
        if [ -n "$ENGINE_PID" ] && kill -0 "$ENGINE_PID" 2>/dev/null; then
            kill -9 "$ENGINE_PID" 2>/dev/null
            sleep 1
        fi

        sleep "$RESTART_DELAY"
        start_engine
    fi
done
