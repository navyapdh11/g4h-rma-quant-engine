#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Deployment Script
# ===============================================
# Usage: ./deploy.sh [OPTIONS]
# Options:
#   --install     Install dependencies
#   --docker      Deploy with Docker
#   --systemd     Deploy with systemd
#   --validate    Validate configuration
#   --start       Start the service
#   --stop        Stop the service
#   --restart     Restart the service
#   --status      Show service status
#   --logs        Show logs
#   --uninstall   Remove deployment

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_FILE="$SCRIPT_DIR/g4h-quant-engine.service"
ENV_FILE="$SCRIPT_DIR/.env"

# Functions
info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[✓]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[!]${NC}    $1"; }
error()   { echo -e "${RED}[✗]${NC}    $1"; }

print_banner() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   G4H-RMA Quant Engine V6.0 — Deployment Script     ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

check_root() {
    if [ "$EUID" -ne 0 ] && [[ "$*" == *"--systemd"* ]]; then
        error "Systemd deployment requires root privileges"
        echo "  Run with: sudo ./deploy.sh --systemd"
        exit 1
    fi
}

install_dependencies() {
    info "Installing dependencies..."
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        error "Python 3 not found"
        exit 1
    fi
    success "Python 3 found: $(python3 --version)"
    
    # Create virtual environment
    if [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
        success "Virtual environment created"
    else
        success "Virtual environment exists"
    fi
    
    # Activate and install
    source "$VENV_DIR/bin/activate"
    info "Installing Python packages..."
    pip install --upgrade pip
    pip install -r "$SCRIPT_DIR/requirements.txt"
    success "Dependencies installed"
}

deploy_docker() {
    info "Deploying with Docker..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        error "Docker not found"
        exit 1
    fi
    success "Docker found: $(docker --version)"
    
    # Check docker-compose
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        error "docker-compose not found"
        exit 1
    fi
    
    # Create logs directory
    mkdir -p "$SCRIPT_DIR/logs"
    mkdir -p "$SCRIPT_DIR/data"
    
    # Build and start
    info "Building Docker image..."
    cd "$SCRIPT_DIR"
    
    if command -v docker-compose &> /dev/null; then
        docker-compose up -d --build
    else
        docker compose up -d --build
    fi
    
    success "Docker deployment complete"
    info "Access the API at: http://localhost:8000"
}

deploy_systemd() {
    info "Deploying with systemd..."
    
    # Check .env file
    if [ ! -f "$ENV_FILE" ]; then
        warn ".env file not found — creating from .env.example"
        if [ -f "$SCRIPT_DIR/.env.example" ]; then
            cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
        else
            cat > "$ENV_FILE" << 'EOF'
# G4H-RMA Quant Engine V6.0 — Environment Configuration
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
APCA_API_BASE_URL=https://paper-api.alpaca.markets
DASHSCOPE_API_KEY=
EOF
        fi
    fi
    success "Environment file ready"
    
    # Create directories
    mkdir -p "$SCRIPT_DIR/logs"
    mkdir -p "$SCRIPT_DIR/data"
    
    # Install service file
    info "Installing systemd service..."
    cp "$SERVICE_FILE" /etc/systemd/system/
    systemctl daemon-reload
    success "Systemd service installed"
    
    # Enable and start
    info "Enabling service..."
    systemctl enable g4h-quant-engine
    success "Service enabled"
    
    info "Starting service..."
    systemctl start g4h-quant-engine
    success "Service started"
    
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "  Deployment Complete!"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Service Status: sudo systemctl status g4h-quant-engine"
    echo "  View Logs:      journalctl -u g4h-quant-engine -f"
    echo "  API Endpoint:   http://localhost:8000"
    echo "  Health Check:   curl http://localhost:8000/health"
    echo ""
}

validate_config() {
    info "Validating configuration..."
    source "$VENV_DIR/bin/activate"
    cd "$SCRIPT_DIR"
    python main.py --validate
    success "Configuration valid"
}

start_service() {
    info "Starting service..."
    
    if systemctl is-active --quiet g4h-quant-engine 2>/dev/null; then
        systemctl start g4h-quant-engine
        success "Systemd service started"
    elif docker ps | grep -q g4h-rma-quant-engine; then
        cd "$SCRIPT_DIR"
        if command -v docker-compose &> /dev/null; then
            docker-compose start
        else
            docker compose start
        fi
        success "Docker container started"
    else
        # Start manually
        source "$VENV_DIR/bin/activate"
        cd "$SCRIPT_DIR"
        nohup python main.py > logs/startup.log 2>&1 &
        success "Service started in background (PID: $!)"
    fi
}

stop_service() {
    info "Stopping service..."
    
    if systemctl is-active --quiet g4h-quant-engine 2>/dev/null; then
        systemctl stop g4h-quant-engine
        success "Systemd service stopped"
    elif docker ps | grep -q g4h-rma-quant-engine; then
        cd "$SCRIPT_DIR"
        if command -v docker-compose &> /dev/null; then
            docker-compose stop
        else
            docker compose stop
        fi
        success "Docker container stopped"
    else
        pkill -f "python main.py" 2>/dev/null || true
        success "Manual process stopped"
    fi
}

show_status() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "  G4H-RMA Quant Engine V6.0 — Status"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Check systemd
    if systemctl is-active --quiet g4h-quant-engine 2>/dev/null; then
        echo -e "  Systemd Service: ${GREEN}ACTIVE${NC}"
        systemctl status g4h-quant-engine --no-pager -l
    fi
    
    # Check Docker
    if docker ps | grep -q g4h-rma-quant-engine; then
        echo -e "  Docker Container:  ${GREEN}RUNNING${NC}"
        docker ps --filter name=g4h-rma-quant-engine
    fi
    
    # Check manual process
    if pgrep -f "python main.py" > /dev/null; then
        echo -e "  Manual Process:    ${GREEN}RUNNING${NC}"
        pgrep -af "python main.py"
    fi
    
    # Health check
    echo ""
    info "Health Check:"
    curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool || warn "API not responding"
    echo ""
}

show_logs() {
    if systemctl is-active --quiet g4h-quant-engine 2>/dev/null; then
        journalctl -u g4h-quant-engine -f --no-pager
    elif [ -f "$SCRIPT_DIR/logs/startup.log" ]; then
        tail -f "$SCRIPT_DIR/logs/startup.log"
    else
        warn "No logs found"
    fi
}

uninstall() {
    warn "This will remove the deployment. Continue? (y/n)"
    read -r confirm
    if [ "$confirm" != "y" ]; then
        info "Cancelled"
        exit 0
    fi
    
    info "Uninstalling..."
    
    # Stop service
    stop_service 2>/dev/null || true
    
    # Remove systemd
    if [ -f /etc/systemd/system/g4h-quant-engine.service ]; then
        systemctl stop g4h-quant-engine 2>/dev/null || true
        systemctl disable g4h-quant-engine 2>/dev/null || true
        rm /etc/systemd/system/g4h-quant-engine.service
        systemctl daemon-reload
        success "Systemd service removed"
    fi
    
    # Remove Docker
    if docker ps -a | grep -q g4h-rma-quant-engine; then
        cd "$SCRIPT_DIR"
        if command -v docker-compose &> /dev/null; then
            docker-compose down --remove-orphans
        else
            docker compose down --remove-orphans
        fi
        success "Docker containers removed"
    fi
    
    success "Uninstallation complete"
}

show_help() {
    print_banner
    echo "Usage: ./deploy.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --install     Install dependencies and create virtual environment"
    echo "  --docker      Deploy using Docker Compose"
    echo "  --systemd     Deploy as systemd service (requires root)"
    echo "  --validate    Validate configuration"
    echo "  --start       Start the service"
    echo "  --stop        Stop the service"
    echo "  --restart     Restart the service"
    echo "  --status      Show service status and health"
    echo "  --logs        Show/follow logs"
    echo "  --uninstall   Remove deployment"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./deploy.sh --install --validate --start"
    echo "  sudo ./deploy.sh --systemd"
    echo "  ./deploy.sh --docker"
    echo ""
}

# Main
print_banner
check_root

if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --install)
            install_dependencies
            shift
            ;;
        --docker)
            deploy_docker
            shift
            ;;
        --systemd)
            deploy_systemd
            shift
            ;;
        --validate)
            validate_config
            shift
            ;;
        --start)
            start_service
            shift
            ;;
        --stop)
            stop_service
            shift
            ;;
        --restart)
            stop_service
            sleep 2
            start_service
            shift
            ;;
        --status)
            show_status
            shift
            ;;
        --logs)
            show_logs
            shift
            ;;
        --uninstall)
            uninstall
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

echo ""
success "Deployment operations complete!"
echo ""
