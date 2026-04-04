#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Manual Startup Script
# For environments without systemd (PRoot, containers, etc.)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[✓]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[!]${NC}    $1"; }
error()   { echo -e "${RED}[✗]${NC}    $1"; }

# Check if already running
if pgrep -f "python main.py" > /dev/null 2>&1; then
    PID=$(pgrep -f "python main.py")
    warn "Engine already on (PID: $PID)"
    echo ""
    echo "  Stop:   pkill -f 'python main.py'"
    echo "  Logs:   tail -f logs/g4h-quant-engine.log"
    echo "  Health: curl http://localhost:8000/health"
    echo ""
    exit 0
fi

# Activate venv
if [ ! -d "venv" ]; then
    error "Virtual environment not found"
    exit 1
fi

source venv/bin/activate

# Create logs dir
mkdir -p logs data

# Start
info "Starting G4H-RMA Quant Engine V6.0..."
nohup python main.py > logs/g4h-quant-engine.log 2>&1 &
PID=$!

sleep 2

if kill -0 $PID 2>/dev/null; then
    success "Engine started (PID: $PID)"
    echo ""
    echo "  API:    http://localhost:8000"
    echo "  Docs:   http://localhost:8000/docs"
    echo "  Logs:   tail -f logs/g4h-quant-engine.log"
    echo "  Stop:   pkill -f 'python main.py'"
    echo "  Health: curl http://localhost:8000/health"
    echo ""
else
    error "Failed to start. Check logs:"
    cat logs/g4h-quant-engine.log
    exit 1
fi
