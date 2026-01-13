#!/usr/bin/env bash
#
# AI-AtlasForge Installation Script
# https://github.com/DragonShadows1978/AI-AtlasForge
#
# Usage: ./install.sh [options]
#   --no-venv     Skip virtual environment creation
#   --no-services Skip systemd service installation prompt
#   --help        Show this help message
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
MIN_PYTHON_VERSION="3.10"
ATLASFORGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse arguments
NO_VENV=false
NO_SERVICES=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-venv)
            NO_VENV=true
            shift
            ;;
        --no-services)
            NO_SERVICES=true
            shift
            ;;
        --help)
            echo "AI-AtlasForge Installation Script"
            echo ""
            echo "Usage: ./install.sh [options]"
            echo "  --no-venv     Skip virtual environment creation"
            echo "  --no-services Skip systemd service installation prompt"
            echo "  --help        Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Helper functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_command() {
    if command -v "$1" &> /dev/null; then
        return 0
    else
        return 1
    fi
}

version_ge() {
    # Returns 0 if $1 >= $2
    printf '%s\n%s' "$2" "$1" | sort -V -C
}

# Banner
echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}           ${GREEN}AI-AtlasForge Installation${NC}                     ${BLUE}║${NC}"
echo -e "${BLUE}║${NC}     Autonomous AI Research & Development Platform        ${BLUE}║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check prerequisites
log_info "Checking prerequisites..."

# Python check
if check_command python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if version_ge "$PYTHON_VERSION" "$MIN_PYTHON_VERSION"; then
        log_success "Python $PYTHON_VERSION found"
    else
        log_error "Python $MIN_PYTHON_VERSION or higher required (found $PYTHON_VERSION)"
        exit 1
    fi
else
    log_error "Python 3 not found. Please install Python $MIN_PYTHON_VERSION+"
    exit 1
fi

# pip check
if check_command pip3 || python3 -m pip --version &> /dev/null; then
    log_success "pip found"
else
    log_error "pip not found. Please install pip"
    exit 1
fi

# git check
if check_command git; then
    log_success "git found"
else
    log_warning "git not found (optional, but recommended)"
fi

# Node.js check (optional)
if check_command node; then
    NODE_VERSION=$(node --version | sed 's/v//')
    log_success "Node.js $NODE_VERSION found"
else
    log_warning "Node.js not found (optional, needed for dashboard JS build)"
fi

echo ""
log_info "Installing to: $ATLASFORGE_ROOT"

# Create virtual environment
if [ "$NO_VENV" = false ]; then
    echo ""
    log_info "Creating Python virtual environment..."
    if [ -d "$ATLASFORGE_ROOT/venv" ]; then
        log_warning "Virtual environment already exists, skipping creation"
    else
        python3 -m venv "$ATLASFORGE_ROOT/venv"
        log_success "Virtual environment created at $ATLASFORGE_ROOT/venv"
    fi

    # Activate venv for the rest of the script
    source "$ATLASFORGE_ROOT/venv/bin/activate"
    log_success "Virtual environment activated"
fi

# Install Python dependencies
echo ""
log_info "Installing Python dependencies..."
if [ -f "$ATLASFORGE_ROOT/requirements.txt" ]; then
    pip install --upgrade pip
    pip install -r "$ATLASFORGE_ROOT/requirements.txt"
    log_success "Python dependencies installed"
else
    log_error "requirements.txt not found"
    exit 1
fi

# Install Node.js dependencies (if npm available)
if check_command npm && [ -f "$ATLASFORGE_ROOT/package.json" ]; then
    echo ""
    log_info "Installing Node.js dependencies..."
    cd "$ATLASFORGE_ROOT"
    npm install
    log_success "Node.js dependencies installed"

    # Build dashboard JS
    if [ -f "$ATLASFORGE_ROOT/dashboard_static/build.js" ]; then
        log_info "Building dashboard JavaScript..."
        node dashboard_static/build.js
        log_success "Dashboard JavaScript built"
    fi
fi

# Create required directories
echo ""
log_info "Creating directory structure..."
directories=(
    "state"
    "logs"
    "workspace"
    "workspace/artifacts"
    "workspace/research"
    "workspace/tests"
    "missions"
    "missions/mission_logs"
    "backups"
    "backups/auto_backups"
    "atlasforge_data"
    "atlasforge_data/knowledge_base"
    "atlasforge_data/analytics"
    "atlasforge_data/exploration"
    "screenshots"
)

for dir in "${directories[@]}"; do
    mkdir -p "$ATLASFORGE_ROOT/$dir"
done
log_success "Directory structure created"

# Create configuration files if they don't exist
echo ""
log_info "Setting up configuration..."
if [ ! -f "$ATLASFORGE_ROOT/config.yaml" ] && [ -f "$ATLASFORGE_ROOT/config.example.yaml" ]; then
    cp "$ATLASFORGE_ROOT/config.example.yaml" "$ATLASFORGE_ROOT/config.yaml"
    log_success "Created config.yaml from template"
    log_warning "Edit config.yaml to add your Anthropic API key"
fi

if [ ! -f "$ATLASFORGE_ROOT/.env" ] && [ -f "$ATLASFORGE_ROOT/.env.example" ]; then
    cp "$ATLASFORGE_ROOT/.env.example" "$ATLASFORGE_ROOT/.env"
    log_success "Created .env from template"
    log_warning "Edit .env to add your Anthropic API key"
fi

# Generate ENVIRONMENT.md
if [ -f "$ATLASFORGE_ROOT/scripts/generate_environment.py" ]; then
    echo ""
    log_info "Detecting hardware and generating ENVIRONMENT.md..."
    python3 "$ATLASFORGE_ROOT/scripts/generate_environment.py"
    log_success "ENVIRONMENT.md generated"
fi

# API key prompt
echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}IMPORTANT: API Key Configuration${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "AI-AtlasForge requires a Claude API key to function."
echo "You can get one at: https://console.anthropic.com/"
echo ""
echo "Configure your API key in one of these ways:"
echo "  1. Edit config.yaml and set 'anthropic_api_key'"
echo "  2. Edit .env and set 'ANTHROPIC_API_KEY'"
echo "  3. Export the environment variable:"
echo "     export ANTHROPIC_API_KEY='your-key-here'"
echo ""

# Ollama prompt
read -p "Do you want to configure Ollama for local LLM support? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if check_command ollama; then
        log_success "Ollama is installed"
        # Update config to enable Ollama
        if [ -f "$ATLASFORGE_ROOT/config.yaml" ]; then
            sed -i 's/enabled: false/enabled: true/' "$ATLASFORGE_ROOT/config.yaml" 2>/dev/null || true
            log_success "Ollama enabled in config.yaml"
        fi
    else
        log_warning "Ollama not found. Install it from: https://ollama.ai/"
        log_info "You can enable Ollama later by setting 'ollama.enabled: true' in config.yaml"
    fi
fi

# systemd services prompt
if [ "$NO_SERVICES" = false ]; then
    echo ""
    read -p "Do you want to install systemd services for auto-start? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ -f "$ATLASFORGE_ROOT/scripts/setup_services.sh" ]; then
            log_info "Running service installation..."
            bash "$ATLASFORGE_ROOT/scripts/setup_services.sh"
        else
            log_warning "Service setup script not found"
        fi
    fi
fi

# Final summary
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "To start using AI-AtlasForge:"
echo ""
if [ "$NO_VENV" = false ]; then
    echo "  1. Activate the virtual environment:"
    echo "     source $ATLASFORGE_ROOT/venv/bin/activate"
    echo ""
fi
echo "  2. Configure your API key (see above)"
echo ""
echo "  3. Start the dashboard:"
echo "     python3 dashboard_v2.py"
echo "     Then open http://localhost:5050 in your browser"
echo ""
echo "  4. Create a mission via the dashboard and start it:"
echo "     python3 claude_autonomous.py --mode=rd"
echo ""
echo "Documentation:"
echo "  - INSTALL.md - Detailed installation guide"
echo "  - USAGE.md - How to use AI-AtlasForge"
echo "  - README.md - Project overview"
echo ""
echo -e "${BLUE}Happy researching!${NC}"
