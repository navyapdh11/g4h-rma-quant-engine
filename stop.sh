#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Stop Script

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

if pgrep -f "python main.py" > /dev/null 2>&1; then
    PID=$(pgrep -f "python main.py")
    kill $PID 2>/dev/null
    sleep 1
    
    if kill -0 $PID 2>/dev/null; then
        kill -9 $PID 2>/dev/null
        echo -e "${RED}[✗]${NC}    Force killed (PID: $PID)"
    else
        echo -e "${GREEN}[✓]${NC}    Stopped (PID: $PID)"
    fi
else
    echo -e "${GREEN}[✓]${NC}    Not running"
fi
