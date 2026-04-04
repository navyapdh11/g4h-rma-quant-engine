#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Status Check
# ===========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  G4H-RMA Quant Engine V6.0 — Status"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Watcher status
if [ -f ".scheduler.pid" ]; then
    SPID=$(cat .scheduler.pid)
    if kill -0 "$SPID" 2>/dev/null; then
        echo -e "  Daily Scheduler:   ${GREEN}RUNNING${NC} (PID: $SPID, 23:00 daily)"
    else
        echo -e "  Daily Scheduler:   ${RED}DEAD${NC} (stale PID)"
    fi
else
    echo -e "  Daily Scheduler:   ${YELLOW}NOT ACTIVE${NC}"
fi

# Watcher status
if [ -f ".watcher.pid" ]; then
    WPID=$(cat .watcher.pid)
    if kill -0 "$WPID" 2>/dev/null; then
        echo -e "  Watcher Daemon:  ${GREEN}RUNNING${NC} (PID: $WPID)"
    else
        echo -e "  Watcher Daemon:  ${RED}DEAD${NC} (stale PID)"
    fi
else
    echo -e "  Watcher Daemon:  ${YELLOW}NOT DEPLOYED${NC}"
fi

# Engine status
if pgrep -f "python main.py" > /dev/null 2>&1; then
    EPID=$(pgrep -f "python main.py")
    echo -e "  Engine Process:    ${GREEN}RUNNING${NC} (PID: $EPID)"
else
    echo -e "  Engine Process:    ${RED}STOPPED${NC}"
fi

# API health
echo ""
echo -e "  ${BLUE}API Health:${NC}"
HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null)
if [ -n "$HEALTH" ]; then
    echo "$HEALTH" | python3 -c "
import sys, json
h = json.load(sys.stdin)
print(f\"    Status:    {h.get('status', 'unknown')}\")
print(f\"    Version:   {h.get('version', 'unknown')}\")
print(f\"    Uptime:    {h.get('uptime_seconds', 0):.0f}s\")
mods = h.get('modules', {})
for k, v in mods.items():
    icon = '✓' if v else '✗'
    print(f\"    {k:12s} {icon}\")
" 2>/dev/null || echo "    Response parse error"
else
    echo -e "    ${RED}UNREACHABLE${NC}"
fi

# Disk usage
echo ""
echo -e "  ${BLUE}Resources:${NC}"
if [ -f "logs/g4h-quant-engine.log" ]; then
    LOGLINE=$(du -h logs/g4h-quant-engine.log 2>/dev/null | cut -f1)
    echo "    Engine Log:  $LOGLINE"
fi
if [ -f "logs/watcher.log" ]; then
    LOGLINE=$(du -h logs/watcher.log 2>/dev/null | cut -f1)
    echo "    Watcher Log: $LOGLINE"
fi
if [ -f "logs/scheduler.log" ]; then
    LOGLINE=$(du -h logs/scheduler.log 2>/dev/null | cut -f1)
    echo "    Scheduler:   $LOGLINE"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
