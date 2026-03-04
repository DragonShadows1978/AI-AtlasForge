#!/usr/bin/env bash
#
# AI-AtlasForge systemd Service Setup
# Installs and configures systemd services for auto-start
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Determine paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATLASFORGE_ROOT="$(dirname "$SCRIPT_DIR")"
SYSTEMD_DIR="$ATLASFORGE_ROOT/systemd"
CURRENT_USER="$(whoami)"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    log_error "Do not run this script as root. It will use sudo when needed."
    exit 1
fi

# Check for systemd
if ! command -v systemctl &> /dev/null; then
    log_error "systemd not found. This script requires systemd."
    exit 1
fi

echo ""
log_info "AI-AtlasForge Service Setup"
log_info "Installation directory: $ATLASFORGE_ROOT"
log_info "Current user: $CURRENT_USER"
echo ""

# Check if service files exist
if [ ! -f "$SYSTEMD_DIR/atlasforge-dashboard.service" ]; then
    log_error "Service file not found: $SYSTEMD_DIR/atlasforge-dashboard.service"
    exit 1
fi

# Create temporary service files with correct paths
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Generate dashboard service
log_info "Generating dashboard service file..."
sed -e "s|/opt/ai-atlasforge|$ATLASFORGE_ROOT|g" \
    -e "s|%i|$CURRENT_USER|g" \
    "$SYSTEMD_DIR/atlasforge-dashboard.service" > "$TEMP_DIR/atlasforge-dashboard.service"

# Generate tray service (if exists)
if [ -f "$SYSTEMD_DIR/atlasforge-tray.service" ]; then
    log_info "Generating tray service file..."
    sed -e "s|/opt/ai-atlasforge|$ATLASFORGE_ROOT|g" \
        -e "s|%i|$CURRENT_USER|g" \
        "$SYSTEMD_DIR/atlasforge-tray.service" > "$TEMP_DIR/atlasforge-tray.service"
fi

# Install services
echo ""
log_info "Installing services (requires sudo)..."

sudo cp "$TEMP_DIR/atlasforge-dashboard.service" /etc/systemd/system/
log_success "Dashboard service installed"

if [ -f "$TEMP_DIR/atlasforge-tray.service" ]; then
    sudo cp "$TEMP_DIR/atlasforge-tray.service" /etc/systemd/system/
    log_success "Tray service installed"
fi

# Reload systemd
log_info "Reloading systemd..."
sudo systemctl daemon-reload
log_success "systemd reloaded"

# Enable services
echo ""
read -p "Enable dashboard service to start on boot? [Y/n] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    sudo systemctl enable atlasforge-dashboard.service
    log_success "Dashboard service enabled"
fi

if [ -f "$TEMP_DIR/atlasforge-tray.service" ]; then
    read -p "Enable tray service to start on login? [Y/n] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo systemctl enable atlasforge-tray.service
        log_success "Tray service enabled"
    fi
fi

# Start services
echo ""
read -p "Start dashboard service now? [Y/n] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    sudo systemctl start atlasforge-dashboard.service
    sleep 2
    if sudo systemctl is-active --quiet atlasforge-dashboard.service; then
        log_success "Dashboard service started"
        echo ""
        echo "Dashboard is running at: http://localhost:5050"
    else
        log_error "Dashboard service failed to start"
        echo "Check logs with: sudo journalctl -u atlasforge-dashboard.service"
    fi
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Service Setup Complete${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "Useful commands:"
echo "  Start dashboard:   sudo systemctl start atlasforge-dashboard"
echo "  Stop dashboard:    sudo systemctl stop atlasforge-dashboard"
echo "  Status:            sudo systemctl status atlasforge-dashboard"
echo "  View logs:         sudo journalctl -u atlasforge-dashboard -f"
echo ""
