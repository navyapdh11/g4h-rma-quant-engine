#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Monitoring & Health Check Script
# =============================================================
# Usage: ./monitor.sh [--watch] [--alert] [--report]
# Options:
#   --watch   Continuous monitoring mode
#   --alert   Send alerts on issues
#   --report  Generate health report

set -e

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
HEALTH_ENDPOINT="$API_URL/health"
RISK_ENDPOINT="$API_URL/api/v1/risk/metrics"
LOG_FILE="${LOG_FILE:-logs/monitor.log}"
ALERT_THRESHOLD_ERRORS=3
ALERT_THRESHOLD_DRAWDOWN=0.05

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Functions
log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_FILE"
}

check_health() {
    local response
    response=$(curl -s -w "\n%{http_code}" "$HEALTH_ENDPOINT" 2>/dev/null)
    local body=$(echo "$response" | head -n -1)
    local status_code=$(echo "$response" | tail -n 1)
    
    if [ "$status_code" != "200" ]; then
        echo -e "${RED}UNHEALTHY${NC} (HTTP $status_code)"
        return 1
    fi
    
    local version=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null)
    local uptime=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('uptime_seconds',0))" 2>/dev/null)
    local kalman=$(echo "$body" | python3 -c "import sys,json; print('✓' if json.load(sys.stdin).get('modules',{}).get('kalman') else '✗')" 2>/dev/null)
    local egarch=$(echo "$body" | python3 -c "import sys,json; print('✓' if json.load(sys.stdin).get('modules',{}).get('egarch') else '✗')" 2>/dev/null)
    local mcts=$(echo "$body" | python3 -c "import sys,json; print('✓' if json.load(sys.stdin).get('modules',{}).get('mcts') else '✗')" 2>/dev/null)
    local alpaca=$(echo "$body" | python3 -c "import sys,json; print('✓' if json.load(sys.stdin).get('modules',{}).get('alpaca') else '○')" 2>/dev/null)
    
    echo -e "${GREEN}HEALTHY${NC} (v$version, uptime: ${uptime}s)"
    echo "  Modules: Kalman=$kalman EGARCH=$egarch MCTS=$mcts Alpaca=$alpaca"
    return 0
}

check_risk_metrics() {
    local response
    response=$(curl -s "$RISK_ENDPOINT" 2>/dev/null)
    
    if [ -z "$response" ]; then
        echo -e "${YELLOW}WARNING${NC}: Could not fetch risk metrics"
        return 1
    fi
    
    local drawdown=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('drawdown',0))" 2>/dev/null)
    local crisis=$(echo "$response" | python3 -c "import sys,json; print('⚠️' if json.load(sys.stdin).get('crisis_mode') else '✓')" 2>/dev/null)
    local positions=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('active_positions',0))" 2>/dev/null)
    local daily_trades=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('daily_trades',0))" 2>/dev/null)
    local var95=$(echo "$response" | python3 -c "import sys,json; v=json.load(sys.stdin).get('var_95'); print(v if v else 'N/A')" 2>/dev/null)
    
    echo "  Risk Metrics:"
    echo "    Drawdown: $drawdown"
    echo "    Crisis Mode: $crisis"
    echo "    Active Positions: $positions"
    echo "    Daily Trades: $daily_trades"
    echo "    VaR (95%): $var95"
    
    # Check thresholds
    if (( $(echo "$drawdown > $ALERT_THRESHOLD_DRAWDOWN" | bc -l 2>/dev/null || echo 0) )); then
        echo -e "    ${RED}⚠️  HIGH DRAWDOWN ALERT${NC}"
        return 1
    fi
    
    return 0
}

check_process() {
    if pgrep -f "python main.py" > /dev/null; then
        local pid=$(pgrep -f "python main.py")
        echo -e "${GREEN}RUNNING${NC} (PID: $pid)"
        return 0
    elif systemctl is-active --quiet g4h-quant-engine 2>/dev/null; then
        echo -e "${GREEN}RUNNING${NC} (systemd)"
        return 0
    elif docker ps | grep -q g4h-rma-quant-engine 2>/dev/null; then
        echo -e "${GREEN}RUNNING${NC} (Docker)"
        return 0
    else
        echo -e "${RED}NOT RUNNING${NC}"
        return 1
    fi
}

send_alert() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    log "🚨 ALERT: $message"
    
    # Slack alert (if configured)
    if [ -n "$SLACK_WEBHOOK_URL" ]; then
        curl -s -X POST "$SLACK_WEBHOOK_URL" \
            -H 'Content-Type: application/json' \
            -d "{\"text\":\"🚨 G4H Quant Engine Alert: $message\"}" \
            > /dev/null 2>&1 || true
    fi
    
    # Email alert (if configured)
    if [ -n "$ALERT_EMAIL" ] && command -v mail &> /dev/null; then
        echo "$message" | mail -s "G4H Quant Engine Alert" "$ALERT_EMAIL" 2>/dev/null || true
    fi
}

generate_report() {
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   G4H-RMA Quant Engine V6.0 — Health Report         ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo ""
    echo "  Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Hostname:  $(hostname)"
    echo ""
    
    echo "  ─────────────────────────────────────────────────────"
    echo "  Process Status:"
    echo -n "    "
    check_process
    echo ""
    
    echo "  ─────────────────────────────────────────────────────"
    echo "  API Health:"
    echo -n "    "
    check_health
    echo ""
    
    echo "  ─────────────────────────────────────────────────────"
    echo "  Risk Metrics:"
    check_risk_metrics
    echo ""
    
    echo "  ─────────────────────────────────────────────────────"
    echo "  System Resources:"
    echo "    CPU: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' 2>/dev/null || echo 'N/A')%"
    echo "    Memory: $(free -h | grep Mem | awk '{print $3 "/" $2}' 2>/dev/null || echo 'N/A')"
    echo "    Disk: $(df -h / | tail -1 | awk '{print $3 "/" $2 " (" $5 ")"}' 2>/dev/null || echo 'N/A')"
    echo ""
    
    echo "  ─────────────────────────────────────────────────────"
    echo "  Recent Activity (last 5 log entries):"
    if [ -f "logs/g4h-quant-engine.log" ]; then
        tail -5 logs/g4h-quant-engine.log 2>/dev/null | sed 's/^/    /'
    else
        echo "    No log file found"
    fi
    echo ""
}

watch_mode() {
    local interval="${WATCH_INTERVAL:-30}"
    log "Starting continuous monitoring (interval: ${interval}s)..."
    
    while true; do
        clear
        echo "╔══════════════════════════════════════════════════════╗"
        echo "║   G4H-RMA Quant Engine V6.0 — Live Monitor          ║"
        echo "╚══════════════════════════════════════════════════════╝"
        echo ""
        echo "  Updated: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "  Press Ctrl+C to exit"
        echo ""
        
        echo "  Process: "
        check_process
        echo ""
        
        echo "  API Health: "
        if ! check_health; then
            send_alert "API health check failed"
        fi
        echo ""
        
        echo "  Risk Metrics:"
        check_risk_metrics
        echo ""
        
        echo "  ─────────────────────────────────────────────────────"
        echo "  Next check in ${interval}s..."
        
        sleep "$interval"
    done
}

# Main
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p logs

case "${1:-}" in
    --watch)
        watch_mode
        ;;
    --alert)
        log "Testing alert system..."
        send_alert "Test alert from G4H Quant Engine"
        echo "Alert sent (if configured)"
        ;;
    --report)
        generate_report
        ;;
    *)
        echo ""
        echo "G4H-RMA Quant Engine V6.0 — Monitoring Script"
        echo "=============================================="
        echo ""
        echo "  Usage: ./monitor.sh [OPTIONS]"
        echo ""
        echo "  Options:"
        echo "    --watch   Continuous monitoring mode"
        echo "    --alert   Test alert system"
        echo "    --report  Generate full health report"
        echo ""
        echo "  Quick Health Check:"
        echo "  ─────────────────────────────────────────"
        echo -n "  Process:  "
        check_process
        echo -n "  API:      "
        check_health
        echo ""
        ;;
esac
