#!/bin/bash
# =============================================
# G4H-RMA Quant Engine V5.0 — Alpaca Starter
# =============================================
# Secure startup script with validation
# Paper trading by default (SAFE!)
# =============================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   G4H-RMA Quant Engine V5.0 — Alpaca Starter      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════╝${NC}"
echo ""

# === FUNCTION: Print status messages ===
info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[✓]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[!]${NC}    $1"; }
error()   { echo -e "${RED}[✗]${NC}    $1"; }

# === FUNCTION: Check if API keys are configured ===
check_api_keys() {
    if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
        warn "API keys not set in environment"
        
        # Check for .env file
        if [ -f ".env" ]; then
            info "Loading keys from .env file..."
            export $(grep -v '^#' .env | xargs)
        else
            echo ""
            echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
            echo "  ⚠️  ALPACA API KEYS NOT CONFIGURED"
            echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
            echo ""
            echo "  Starting in SIMULATION mode (no real trades)."
            echo ""
            echo "  To enable paper trading:"
            echo "  1. Get keys from: https://app.alpaca.markets/paper"
            echo "  2. Create a .env file:"
            echo ""
            echo "     APCA_API_KEY_ID=your_key_here"
            echo "     APCA_API_SECRET_KEY=your_secret_here"
            echo "     APCA_API_BASE_URL=https://paper-api.alpaca.markets"
            echo ""
            echo -e "${YELLOW}  Press Ctrl+C to cancel, or wait 5s to continue in simulation mode...${NC}"
            sleep 5
            return 1
        fi
    fi
    return 0
}

# === FUNCTION: Validate API keys (optional test) ===
validate_api_keys() {
    if [ -n "$APCA_API_KEY_ID" ] && [ -n "$APCA_API_SECRET_KEY" ]; then
        info "Testing Alpaca API connection..."
        
        # Quick test using Python
        if python3 -c "
import os
try:
    import alpaca_trade_api as tradeapi
    api = tradeapi.REST(
        os.getenv('APCA_API_KEY_ID'),
        os.getenv('APCA_API_SECRET_KEY'),
        os.getenv('APCA_API_BASE_URL', 'https://paper-api.alpaca.markets'),
        api_version='v2'
    )
    acc = api.get_account()
    print(f'Connected: {acc.status}')
except Exception as e:
    print(f'Error: {e}')
    exit(1)
" 2>/dev/null; then
            success "Alpaca API connection verified"
            return 0
        else
            warn "Alpaca API connection failed — starting in simulation mode"
            return 1
        fi
    fi
    return 1
}

# === FUNCTION: Check environment ===
check_environment() {
    # Check Python
    if ! command -v python3 &> /dev/null; then
        error "Python 3 not found"
        exit 1
    fi
    success "Python 3 found: $(python3 --version)"
    
    # Check virtual environment
    if [ ! -d "venv" ]; then
        error "Virtual environment not found at: $SCRIPT_DIR/venv"
        echo ""
        echo "  To create it, run:"
        echo "  cd $SCRIPT_DIR"
        echo "  python3 -m venv venv"
        echo "  source venv/bin/activate"
        echo "  pip install -r requirements.txt"
        exit 1
    fi
    success "Virtual environment found"
    
    # Check requirements
    if [ ! -f "venv/lib/python3.13/site-packages/fastapi/__init__.py" ] && \
       [ ! -f "venv/lib/python3.11/site-packages/fastapi/__init__.py" ] && \
       [ ! -f "venv/lib/python3.12/site-packages/fastapi/__init__.py" ]; then
        warn "FastAPI not installed — running pip install..."
        source venv/bin/activate
        pip install -q -r requirements.txt
    fi
}

# === FUNCTION: Stop existing instance ===
stop_existing() {
    info "Stopping any existing instance..."
    if pkill -f "python main.py" 2>/dev/null; then
        success "Stopped existing process"
        sleep 1
    else
        info "No existing process found"
    fi
}

# === FUNCTION: Start the server ===
start_server() {
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "  🚀 STARTING QUANT ENGINE"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Show configuration
    if [ -n "$APCA_API_KEY_ID" ]; then
        key_preview="${APCA_API_KEY_ID:0:6}...${APCA_API_KEY_ID: -4}"
        info "API Key: $key_preview"
        info "Mode: ${APCA_API_BASE_URL:-paper}"
    else
        warn "Running in SIMULATION mode (no API keys)"
    fi
    echo ""
    
    # Start
    source venv/bin/activate
    python main.py
}

# === MAIN EXECUTION ===
echo ""
check_environment
echo ""
check_api_keys
KEYS_VALID=$?
echo ""

if [ $KEYS_VALID -eq 0 ]; then
    validate_api_keys
fi
echo ""

stop_existing
start_server
