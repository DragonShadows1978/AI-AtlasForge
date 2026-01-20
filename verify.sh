#!/usr/bin/env bash
#
# AI-AtlasForge Installation Verification Script
# https://github.com/DragonShadows1978/AI-AtlasForge
#
# Checks that the installation is complete and ready to use.
# Run: ./verify.sh
#
set -uo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
MIN_PYTHON_VERSION="3.10"
ATLASFORGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ATLASFORGE_ROOT/venv"
DASHBOARD_PORT="${ATLASFORGE_PORT:-5050}"

# Counters
PASSED=0
WARNED=0
FAILED=0

# Helper functions
pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASSED++))
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    ((WARNED++))
}

fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAILED++))
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

check_command() {
    command -v "$1" &> /dev/null
}

version_ge() {
    # Returns 0 if $1 >= $2
    printf '%s\n%s' "$2" "$1" | sort -V -C
}

# Banner
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}           AI-AtlasForge Installation Verification${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# =============================================================================
# SECTION 1: System Prerequisites
# =============================================================================
info "Checking system prerequisites..."
echo ""

# Python version check
if check_command python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if version_ge "$PYTHON_VERSION" "$MIN_PYTHON_VERSION"; then
        pass "Python $PYTHON_VERSION (>= $MIN_PYTHON_VERSION required)"
    else
        fail "Python $PYTHON_VERSION found, but $MIN_PYTHON_VERSION+ required"
    fi
else
    fail "Python 3 not found"
fi

# Git check
if check_command git; then
    GIT_VERSION=$(git --version | awk '{print $3}')
    pass "Git $GIT_VERSION"
else
    warn "Git not found (optional, but recommended)"
fi

# Node.js check (optional)
if check_command node; then
    NODE_VERSION=$(node --version | sed 's/v//')
    pass "Node.js $NODE_VERSION (optional, for dashboard JS build)"
else
    warn "Node.js not found (optional, for dashboard JS build)"
fi

# Docker check (optional)
if check_command docker; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    pass "Docker $DOCKER_VERSION (optional, for containerized deployment)"
else
    warn "Docker not found (optional, for containerized deployment)"
fi

echo ""

# =============================================================================
# SECTION 2: Virtual Environment
# =============================================================================
info "Checking virtual environment..."
echo ""

if [ -d "$VENV_DIR" ]; then
    if [ -f "$VENV_DIR/bin/activate" ]; then
        pass "Virtual environment exists at $VENV_DIR"

        # Check if venv Python matches system expectation
        if [ -x "$VENV_DIR/bin/python3" ]; then
            VENV_PYTHON_VERSION=$("$VENV_DIR/bin/python3" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            pass "venv Python version: $VENV_PYTHON_VERSION"
        else
            fail "venv Python executable not found"
        fi
    else
        fail "Virtual environment directory exists but appears corrupted"
    fi
else
    warn "No virtual environment found (run 'make install' or './install.sh')"
fi

echo ""

# =============================================================================
# SECTION 3: Python Dependencies
# =============================================================================
info "Checking Python dependencies..."
echo ""

# Determine which Python to use
if [ -x "$VENV_DIR/bin/python3" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python3"
else
    PYTHON_BIN="python3"
fi

# Core dependencies to check
CORE_DEPS=("flask" "flask_socketio" "anthropic" "pyyaml" "requests")

for dep in "${CORE_DEPS[@]}"; do
    if $PYTHON_BIN -c "import $dep" 2>/dev/null; then
        pass "Python package: $dep"
    else
        fail "Python package missing: $dep"
    fi
done

# Optional dependencies
OPTIONAL_DEPS=("sklearn" "numpy")
for dep in "${OPTIONAL_DEPS[@]}"; do
    if $PYTHON_BIN -c "import $dep" 2>/dev/null; then
        pass "Optional package: $dep"
    else
        warn "Optional package missing: $dep (some features may be limited)"
    fi
done

echo ""

# =============================================================================
# SECTION 4: Directory Structure
# =============================================================================
info "Checking directory structure..."
echo ""

REQUIRED_DIRS=(
    "state"
    "logs"
    "workspace"
    "workspace/artifacts"
    "workspace/research"
    "missions"
    "atlasforge_data"
)

for dir in "${REQUIRED_DIRS[@]}"; do
    if [ -d "$ATLASFORGE_ROOT/$dir" ]; then
        pass "Directory: $dir"
    else
        fail "Missing directory: $dir"
    fi
done

echo ""

# =============================================================================
# SECTION 5: Core Files
# =============================================================================
info "Checking core files..."
echo ""

CORE_FILES=(
    "atlasforge_conductor.py"
    "dashboard_v2.py"
    "af_engine.py"
    "requirements.txt"
)

for file in "${CORE_FILES[@]}"; do
    if [ -f "$ATLASFORGE_ROOT/$file" ]; then
        pass "File: $file"
    else
        fail "Missing file: $file"
    fi
done

echo ""

# =============================================================================
# SECTION 6: API Key Configuration
# =============================================================================
info "Checking API key configuration..."
echo ""

API_KEY_FOUND=false

# Check environment variable
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    pass "API key found in ANTHROPIC_API_KEY environment variable"
    API_KEY_FOUND=true
fi

# Check .env file
if [ -f "$ATLASFORGE_ROOT/.env" ] && grep -q "ANTHROPIC_API_KEY" "$ATLASFORGE_ROOT/.env"; then
    if grep -q "ANTHROPIC_API_KEY=sk-" "$ATLASFORGE_ROOT/.env" 2>/dev/null || \
       grep -qE "ANTHROPIC_API_KEY=['\"]?[a-zA-Z0-9_-]+" "$ATLASFORGE_ROOT/.env" 2>/dev/null; then
        pass "API key found in .env file"
        API_KEY_FOUND=true
    fi
fi

# Check config.yaml
if [ -f "$ATLASFORGE_ROOT/config.yaml" ] && grep -q "anthropic_api_key" "$ATLASFORGE_ROOT/config.yaml"; then
    if grep -qE "anthropic_api_key:.*[a-zA-Z0-9_-]+" "$ATLASFORGE_ROOT/config.yaml" 2>/dev/null; then
        pass "API key found in config.yaml"
        API_KEY_FOUND=true
    fi
fi

if [ "$API_KEY_FOUND" = false ]; then
    fail "No Anthropic API key configured!"
    echo ""
    echo -e "    ${YELLOW}To fix:${NC}"
    echo "    1. Get an API key from https://console.anthropic.com/"
    echo "    2. Configure it using one of:"
    echo "       - export ANTHROPIC_API_KEY='your-key'"
    echo "       - Add to .env file: ANTHROPIC_API_KEY=your-key"
    echo "       - Add to config.yaml: anthropic_api_key: your-key"
fi

echo ""

# =============================================================================
# SECTION 7: Port Availability
# =============================================================================
info "Checking port availability..."
echo ""

# Check if dashboard port is in use
if check_command ss; then
    if ss -tuln | grep -q ":$DASHBOARD_PORT "; then
        warn "Port $DASHBOARD_PORT is in use (dashboard may already be running)"
    else
        pass "Port $DASHBOARD_PORT is available for dashboard"
    fi
elif check_command netstat; then
    if netstat -tuln | grep -q ":$DASHBOARD_PORT "; then
        warn "Port $DASHBOARD_PORT is in use (dashboard may already be running)"
    else
        pass "Port $DASHBOARD_PORT is available for dashboard"
    fi
else
    warn "Cannot check port availability (no ss/netstat)"
fi

echo ""

# =============================================================================
# SECTION 8: Quick Import Test
# =============================================================================
info "Running quick import test..."
echo ""

IMPORT_TEST=$($PYTHON_BIN -c "
import sys
try:
    import af_engine
    import io_utils
    import dashboard_v2
    print('SUCCESS')
except ImportError as e:
    print(f'IMPORT_ERROR: {e}')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1)

if [[ "$IMPORT_TEST" == "SUCCESS" ]]; then
    pass "Core modules import successfully"
else
    fail "Import test failed: $IMPORT_TEST"
fi

echo ""

# =============================================================================
# SUMMARY
# =============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                        Summary${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC}  $PASSED"
echo -e "  ${YELLOW}Warnings:${NC} $WARNED"
echo -e "  ${RED}Failed:${NC}  $FAILED"
echo ""

if [ $FAILED -eq 0 ]; then
    if [ $WARNED -eq 0 ]; then
        echo -e "${GREEN}All checks passed! Your installation is ready.${NC}"
    else
        echo -e "${GREEN}Installation is functional.${NC} Review warnings above."
    fi
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo "  1. Start the dashboard:  make dashboard"
    echo "  2. Open in browser:      http://localhost:$DASHBOARD_PORT"
    echo "  3. Create a mission via the dashboard"
    echo "  4. Start the agent:      make run"
    echo ""
    exit 0
else
    echo -e "${RED}Some checks failed.${NC} Review the errors above."
    echo ""
    echo -e "${BLUE}To fix:${NC}"
    if [ ! -d "$VENV_DIR" ]; then
        echo "  - Run: make install"
    fi
    echo "  - Ensure API key is configured"
    echo "  - Check that all dependencies are installed"
    echo ""
    exit 1
fi
