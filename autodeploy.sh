#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Autodeploy Script
# ==================================================
# One-command deployment with:
#   ✓ Auto-restart on crash
#   ✓ Health monitoring
#   ✓ Log rotation
#   ✓ Persistent across sessions
# ==================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Paths
WATCHER_PID_FILE="$SCRIPT_DIR/.watcher.pid"
ENGINE_PID_FILE="$SCRIPT_DIR/.engine.pid"
SCHEDULER_PID_FILE="$SCRIPT_DIR/.scheduler.pid"
WATCHER_LOG="$SCRIPT_DIR/logs/autodeploy.log"
SCHEDULER_LOG="$SCRIPT_DIR/logs/scheduler.log"

info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
success() { echo -e "${GREEN}[✓]${NC}      $1"; }
warn()    { echo -e "${YELLOW}[!]${NC}      $1"; }
error()   { echo -e "${RED}[✗]${NC}      $1"; }

# ── Banner ──
echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   G4H-RMA Quant Engine V6.0 — Autodeploy               ║"
echo "║   Auto-restart • Health Monitor • Log Rotation          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Stop existing ──
stop_all() {
    # Stop scheduler
    if [ -f "$SCHEDULER_PID_FILE" ]; then
        SPID=$(cat "$SCHEDULER_PID_FILE" 2>/dev/null)
        if kill -0 "$SPID" 2>/dev/null; then
            kill "$SPID" 2>/dev/null
            sleep 1
            success "Stopped scheduler daemon (PID: $SPID)"
        fi
        rm -f "$SCHEDULER_PID_FILE"
    fi

    if [ -f "$WATCHER_PID_FILE" ]; then
        WPID=$(cat "$WATCHER_PID_FILE" 2>/dev/null)
        if kill -0 "$WPID" 2>/dev/null; then
            kill "$WPID" 2>/dev/null
            sleep 1
            success "Stopped watcher daemon (PID: $WPID)"
        fi
        rm -f "$WATCHER_PID_FILE"
    fi

    if pgrep -f "python main.py" > /dev/null 2>&1; then
        pkill -f "python main.py" 2>/dev/null
        sleep 1
        success "Stopped engine process"
    fi
}

# ── Validate environment ──
validate() {
    info "Validating environment..."

    if [ ! -d "venv" ]; then
        error "Virtual environment not found"
        exit 1
    fi
    success "Virtual environment: venv/"

    source venv/bin/activate

    if ! python -c "import fastapi" 2>/dev/null; then
        warn "Installing dependencies..."
        pip install -q -r requirements.txt
    fi
    success "Dependencies installed"

    if ! python -c "from config import settings" 2>/dev/null; then
        error "Configuration validation failed"
        exit 1
    fi
    success "Configuration valid"
}

# ── Setup directories ──
setup_dirs() {
    mkdir -p logs data reports
    success "Directories ready"
}

# ── Rotate logs ──
rotate_logs() {
    local log_file="$1"
    local max_size="${2:-10485760}"  # 10MB default

    if [ -f "$log_file" ]; then
        local size=$(stat -f%z "$log_file" 2>/dev/null || stat -c%s "$log_file" 2>/dev/null || echo 0)
        if [ "$size" -gt "$max_size" ]; then
            mv "$log_file" "${log_file}.old"
            gzip "${log_file}.old" 2>/dev/null || true
            info "Rotated $log_file"
        fi
    fi
}

# ── Create watcher daemon ──
create_watcher() {
    cat > "$SCRIPT_DIR/watcher.sh" << 'WATCHER'
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
WATCHER

    chmod +x "$SCRIPT_DIR/watcher.sh"
    success "Watcher daemon created"
}

# ── Start watcher ──
start_watcher() {
    if [ -f "$WATCHER_PID_FILE" ]; then
        WPID=$(cat "$WATCHER_PID_FILE" 2>/dev/null)
        if kill -0 "$WPID" 2>/dev/null; then
            warn "Watcher already running (PID: $WPID)"
            return 0
        fi
    fi

    info "Starting watcher daemon..."
    nohup bash "$SCRIPT_DIR/watcher.sh" >> "$WATCHER_LOG" 2>&1 &
    echo $! > "$WATCHER_PID_FILE"

    sleep 3

    if kill -0 $(cat "$WATCHER_PID_FILE") 2>/dev/null; then
        success "Watcher daemon started (PID: $(cat "$WATCHER_PID_FILE"))"
    else
        error "Watcher failed to start"
        exit 1
    fi
}

# ── Wait for engine ──
wait_for_engine() {
    info "Waiting for engine to start..."
    for i in $(seq 1 15); do
        sleep 1
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            return 0
        fi
        echo -n "."
    done
    echo ""
    return 1
}

# ── Show status ──
show_status() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "  DEPLOYMENT COMPLETE"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    if [ -f "$WATCHER_PID_FILE" ]; then
        WPID=$(cat "$WATCHER_PID_FILE")
        echo -e "  Watcher Daemon:  ${GREEN}RUNNING${NC} (PID: $WPID)"
    fi

    if [ -f "$ENGINE_PID_FILE" ]; then
        EPID=$(cat "$ENGINE_PID_FILE")
        if kill -0 "$EPID" 2>/dev/null; then
            echo -e "  Engine Process:  ${GREEN}RUNNING${NC} (PID: $EPID)"
        else
            echo -e "  Engine Process:  ${YELLOW}STARTING...${NC}"
        fi
    fi

    echo ""
    echo "  ── Endpoints ──────────────────────────────────"
    echo "  API:     http://localhost:8000"
    echo "  Docs:    http://localhost:8000/docs"
    echo "  Health:  curl http://localhost:8000/health"
    echo ""
    echo "  ── Management ─────────────────────────────────"
    echo "  Stop:    ./stop-autodeploy.sh"
    echo "  Status:  ./status.sh"
    echo "  Logs:    tail -f logs/g4h-quant-engine.log"
    echo "  Watcher: tail -f logs/watcher.log"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ── Start daily scheduler ──
start_scheduler() {
    if [ -f "$SCHEDULER_PID_FILE" ]; then
        SPID=$(cat "$SCHEDULER_PID_FILE" 2>/dev/null)
        if kill -0 "$SPID" 2>/dev/null; then
            success "Daily scheduler already running (PID: $SPID)"
            return 0
        fi
    fi

    info "Starting daily autodeploy scheduler (23:00 daily)..."
    nohup bash "$SCRIPT_DIR/daily-scheduler.sh" >> "$SCHEDULER_LOG" 2>&1 &
    echo $! > "$SCHEDULER_PID_FILE"
    success "Daily scheduler started (PID: $(cat "$SCHEDULER_PID_FILE"))"
}

# ── Main ──
mkdir -p logs

stop_all
validate
setup_dirs
create_watcher
start_watcher
start_scheduler

if wait_for_engine; then
    show_status
else
    error "Engine failed to start. Check logs:"
    tail -20 logs/g4h-quant-engine.log 2>/dev/null
    exit 1
fi
