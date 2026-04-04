#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Stop Autodeploy
# ==============================================
# Stops the watcher daemon and engine process

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

WATCHER_PID_FILE="$SCRIPT_DIR/.watcher.pid"
SCHEDULER_PID_FILE="$SCRIPT_DIR/.scheduler.pid"

# Stop scheduler
if [ -f "$SCHEDULER_PID_FILE" ]; then
    SPID=$(cat "$SCHEDULER_PID_FILE")
    if kill -0 "$SPID" 2>/dev/null; then
        kill "$SPID" 2>/dev/null
        sleep 1
        if kill -0 "$SPID" 2>/dev/null; then
            kill -9 "$SPID" 2>/dev/null
        fi
        echo -e "${GREEN}[✓]${NC} Scheduler stopped (PID: $SPID)"
    else
        echo -e "${YELLOW}[!]${NC} Scheduler not running"
    fi
    rm -f "$SCHEDULER_PID_FILE"
else
    echo -e "${YELLOW}[!]${NC} No scheduler PID file found"
fi

# Stop watcher
if [ -f "$WATCHER_PID_FILE" ]; then
    WPID=$(cat "$WATCHER_PID_FILE")
    if kill -0 "$WPID" 2>/dev/null; then
        kill "$WPID" 2>/dev/null
        sleep 1
        if kill -0 "$WPID" 2>/dev/null; then
            kill -9 "$WPID" 2>/dev/null
        fi
        echo -e "${GREEN}[✓]${NC} Watcher stopped (PID: $WPID)"
    else
        echo -e "${YELLOW}[!]${NC} Watcher not running"
    fi
    rm -f "$WATCHER_PID_FILE"
else
    echo -e "${YELLOW}[!]${NC} No watcher PID file found"
fi

# Stop engine
if pgrep -f "python main.py" > /dev/null 2>&1; then
    pkill -f "python main.py" 2>/dev/null
    sleep 1
    if pgrep -f "python main.py" > /dev/null 2>&1; then
        pkill -9 -f "python main.py" 2>/dev/null
    fi
    echo -e "${GREEN}[✓]${NC} Engine stopped"
else
    echo -e "${YELLOW}[!]${NC} Engine not running"
fi

rm -f "$SCRIPT_DIR/.engine.pid"
echo ""
echo "Deployment stopped."
