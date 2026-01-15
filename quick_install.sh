#!/usr/bin/env bash
#
# AI-AtlasForge Quick Install Script
# https://github.com/DragonShadows1978/AI-AtlasForge
#
# One-liner installation:
#   curl -sSL https://raw.githubusercontent.com/DragonShadows1978/AI-AtlasForge/main/quick_install.sh | bash
#
# Or with custom directory:
#   curl -sSL https://raw.githubusercontent.com/DragonShadows1978/AI-AtlasForge/main/quick_install.sh | bash -s -- ~/my-atlasforge
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/DragonShadows1978/AI-AtlasForge.git"
DEFAULT_INSTALL_DIR="$HOME/AI-AtlasForge"
INSTALL_DIR="${1:-$DEFAULT_INSTALL_DIR}"
MIN_PYTHON_VERSION="3.10"

# Helper functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_command() {
    command -v "$1" &> /dev/null
}

version_ge() {
    printf '%s\n%s' "$2" "$1" | sort -V -C
}

cleanup_on_error() {
    log_error "Installation failed!"
    if [ -d "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "/" ]; then
        log_info "Cleaning up partial installation..."
        rm -rf "$INSTALL_DIR"
    fi
    exit 1
}

# Set trap for cleanup on error
trap cleanup_on_error ERR

# Banner
echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}           ${GREEN}AI-AtlasForge Quick Install${NC}                     ${BLUE}║${NC}"
echo -e "${BLUE}║${NC}     Autonomous AI Research & Development Platform        ${BLUE}║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

# =============================================================================
# STEP 1: Check Prerequisites
# =============================================================================
log_info "Checking prerequisites..."

# Python check
if check_command python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if version_ge "$PYTHON_VERSION" "$MIN_PYTHON_VERSION"; then
        log_success "Python $PYTHON_VERSION"
    else
        log_error "Python $MIN_PYTHON_VERSION+ required (found $PYTHON_VERSION)"
        echo ""
        echo "Install Python 3.10+ and try again."
        exit 1
    fi
else
    log_error "Python 3 not found"
    echo ""
    echo "Please install Python 3.10+ first:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora:        sudo dnf install python3"
    echo "  macOS:         brew install python@3.10"
    exit 1
fi

# Git check
if check_command git; then
    log_success "Git found"
else
    log_error "Git not found"
    echo ""
    echo "Please install Git first:"
    echo "  Ubuntu/Debian: sudo apt install git"
    echo "  Fedora:        sudo dnf install git"
    echo "  macOS:         brew install git"
    exit 1
fi

# curl check (for this script to have been run)
if check_command curl; then
    log_success "curl found"
else
    log_warning "curl not found (you got here somehow though!)"
fi

echo ""

# =============================================================================
# STEP 2: Check Installation Directory
# =============================================================================
log_info "Install directory: $INSTALL_DIR"

if [ -d "$INSTALL_DIR" ]; then
    if [ -f "$INSTALL_DIR/install.sh" ]; then
        log_warning "AI-AtlasForge already exists at $INSTALL_DIR"
        echo ""
        read -p "Do you want to update the existing installation? [y/N] " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Installation cancelled."
            exit 0
        fi
        log_info "Updating existing installation..."
        cd "$INSTALL_DIR"
        git pull
    else
        log_error "Directory exists but doesn't appear to be AI-AtlasForge"
        echo ""
        echo "Please remove it or choose a different directory:"
        echo "  curl -sSL ... | bash -s -- ~/different-path"
        exit 1
    fi
else
    # =============================================================================
    # STEP 3: Clone Repository
    # =============================================================================
    log_info "Cloning AI-AtlasForge..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    log_success "Repository cloned"
    cd "$INSTALL_DIR"
fi

echo ""

# =============================================================================
# STEP 4: Run Installer
# =============================================================================
log_info "Running installation script..."
echo ""

./install.sh --no-services

echo ""

# =============================================================================
# STEP 5: Run Verification
# =============================================================================
if [ -f "./verify.sh" ]; then
    log_info "Running verification..."
    echo ""
    ./verify.sh || true
fi

echo ""

# =============================================================================
# FINAL SUMMARY
# =============================================================================
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}              Quick Install Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "AI-AtlasForge has been installed to:"
echo -e "  ${BLUE}$INSTALL_DIR${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Navigate to the directory:"
echo -e "     ${YELLOW}cd $INSTALL_DIR${NC}"
echo ""
echo "  2. Configure your API key:"
echo -e "     ${YELLOW}export ANTHROPIC_API_KEY='your-key-here'${NC}"
echo "     (Get one at https://console.anthropic.com/)"
echo ""
echo "  3. Start the dashboard:"
echo -e "     ${YELLOW}source venv/bin/activate${NC}"
echo -e "     ${YELLOW}make dashboard${NC}"
echo ""
echo "  4. Open http://localhost:5050 in your browser"
echo ""
echo "  5. Create a mission and run:"
echo -e "     ${YELLOW}make run${NC}"
echo ""
echo "For more commands, run:"
echo -e "  ${YELLOW}make help${NC}"
echo ""
