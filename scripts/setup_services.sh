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

# Unit-file template rewriter: Python helper that is safe under
# paths containing |, &, \, ', and does atomic writes. Symmetric with
# WebProxy/install/rewrite_mcp_paths.py for the MCP-JSON case.
REWRITE_UNIT="$ATLASFORGE_ROOT/WebProxy/install/rewrite_unit_files.py"
if [ ! -f "$REWRITE_UNIT" ]; then
    log_error "Missing unit-file rewriter: $REWRITE_UNIT"
    exit 1
fi

# Generate dashboard service
log_info "Generating dashboard service file..."
python3 "$REWRITE_UNIT" "$ATLASFORGE_ROOT" "$CURRENT_USER" \
    "$SYSTEMD_DIR/atlasforge-dashboard.service" \
    "$TEMP_DIR/atlasforge-dashboard.service"

# Generate tray service (if exists)
if [ -f "$SYSTEMD_DIR/atlasforge-tray.service" ]; then
    log_info "Generating tray service file..."
    python3 "$REWRITE_UNIT" "$ATLASFORGE_ROOT" "$CURRENT_USER" \
        "$SYSTEMD_DIR/atlasforge-tray.service" \
        "$TEMP_DIR/atlasforge-tray.service"
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

# ───────────────────────────────────────────────────────────────
# Web Proxy — installed as a USER-level systemd unit (not system-wide).
# Rationale: the proxy reads the caller's env (BRAVE_API_KEY etc.) and
# runs as the same user that invokes Claude Code / AtlasForge subagents.
# ───────────────────────────────────────────────────────────────
if [ -f "$ATLASFORGE_ROOT/WebProxy/systemd/atlasforge-web-proxy.service" ]; then
    echo ""
    log_info "Generating web proxy user-service file..."
    USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$USER_SYSTEMD_DIR"
    python3 "$REWRITE_UNIT" "$ATLASFORGE_ROOT" "$CURRENT_USER" \
        "$ATLASFORGE_ROOT/WebProxy/systemd/atlasforge-web-proxy.service" \
        "$USER_SYSTEMD_DIR/atlasforge-web-proxy.service"
    systemctl --user daemon-reload
    log_success "Web proxy user-service installed at $USER_SYSTEMD_DIR/atlasforge-web-proxy.service"
fi

# ───────────────────────────────────────────────────────────────
# Rewrite MCP JSON configs to point at the real install path via the
# Python helper (scripts/rewrite_mcp_paths.py). Safer than sed because
# it parses JSON, anchors the replacement, and writes atomically.
# Idempotent: re-running against the same root is a no-op.
# ───────────────────────────────────────────────────────────────
echo ""
log_info "Rewriting MCP config paths to $ATLASFORGE_ROOT..."
if [ -f "$ATLASFORGE_ROOT/WebProxy/install/rewrite_mcp_paths.py" ]; then
    if python3 "$ATLASFORGE_ROOT/WebProxy/install/rewrite_mcp_paths.py" "$ATLASFORGE_ROOT"; then
        log_success "MCP config paths rewritten"
    else
        log_error "Failed to rewrite MCP config paths"
    fi
else
    log_warning "WebProxy/install/rewrite_mcp_paths.py not found — skipping"
fi

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

# Web Proxy enable/start prompts (user unit)
if [ -f "$HOME/.config/systemd/user/atlasforge-web-proxy.service" ]; then
    echo ""
    read -p "Enable web proxy user-service to start on login? [Y/n] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        systemctl --user enable atlasforge-web-proxy.service
        log_success "Web proxy user-service enabled"
    fi

    read -p "Start web proxy service now? [Y/n] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        systemctl --user start atlasforge-web-proxy.service
        sleep 2
        if systemctl --user is-active --quiet atlasforge-web-proxy.service; then
            log_success "Web proxy service started"
            if curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
                log_success "Web proxy health check passed (http://127.0.0.1:8765/health)"
            else
                log_warning "Web proxy started but health check failed — check 'journalctl --user -u atlasforge-web-proxy'"
            fi
        else
            log_error "Web proxy service failed to start"
            echo "Check logs with: journalctl --user -u atlasforge-web-proxy"
        fi
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
echo "  Web proxy:         systemctl --user {start,stop,status} atlasforge-web-proxy"
echo "  Proxy logs:        journalctl --user -u atlasforge-web-proxy -f"
echo "  Proxy health:      curl -sf http://127.0.0.1:8765/health"
echo ""
