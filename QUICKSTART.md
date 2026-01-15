# AI-AtlasForge Quick Start Guide

Get from zero to your first mission in 5 minutes.

## Prerequisites

Before you begin, ensure you have:

- [ ] **Python 3.10+** - Check with `python3 --version`
- [ ] **Git** - Check with `git --version`
- [ ] **Anthropic API Key** - Get one at https://console.anthropic.com/

## Installation

### Option A: One-Liner (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/DragonShadows1978/AI-AtlasForge/main/quick_install.sh | bash
cd ~/AI-AtlasForge
```

### Option B: Manual Install

```bash
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge
./install.sh
```

### Option C: Docker

```bash
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge
docker compose up -d
```

Skip to [Step 3](#3-run-your-first-mission) if using Docker.

## Setup

### 1. Configure API Key

Choose one method:

```bash
# Option 1: Environment variable (recommended for testing)
export ANTHROPIC_API_KEY='sk-ant-...'

# Option 2: .env file (persistent)
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# Option 3: config.yaml
# Edit config.yaml and set anthropic_api_key
```

### 2. Verify Installation

```bash
./verify.sh
# or
make verify
```

You should see green checkmarks for all required components.

### 3. Run Your First Mission

**Start the dashboard** (in terminal 1):
```bash
source venv/bin/activate  # Skip if using Docker
make dashboard
```

Open http://localhost:5050 in your browser.

**Load sample mission** (in terminal 2):
```bash
source venv/bin/activate  # Skip if using Docker
make sample-mission
```

**Start the agent**:
```bash
make run
```

Watch the dashboard as AtlasForge creates a simple "Hello World" script.

## What's Next?

### Create Custom Missions

Via the dashboard:
1. Click "Create Mission"
2. Enter your objective (what you want built)
3. Set cycle budget (1-10, more cycles = more iterations)
4. Click Create

Via command line:
```bash
# Edit state/mission.json or use the dashboard
```

### Common Commands

```bash
make help         # Show all commands
make dashboard    # Start web dashboard
make run          # Start autonomous agent
make verify       # Check installation
make stop         # Stop all processes
make clean        # Clean caches
```

### Monitor Progress

- **Dashboard** - http://localhost:5050 for real-time status
- **Logs** - Check `logs/` directory
- **State** - Current mission in `state/mission.json`

## Troubleshooting

### API Key Not Found

```bash
# Check if key is set
echo $ANTHROPIC_API_KEY

# Or verify with
make check-api
```

### Dashboard Won't Start

```bash
# Check if port is in use
ss -tuln | grep 5050

# Use different port
ATLASFORGE_PORT=5051 make dashboard
```

### Import Errors

```bash
# Reinstall dependencies
source venv/bin/activate
pip install -r requirements.txt
```

## Platform-Specific Notes

### Windows (WSL2)

1. Install WSL2: `wsl --install`
2. Open Ubuntu terminal
3. Follow standard installation

### macOS

Should work but is less tested. Report issues at:
https://github.com/DragonShadows1978/AI-AtlasForge/issues

## Learn More

- [INSTALL.md](INSTALL.md) - Detailed installation options
- [USAGE.md](USAGE.md) - Complete usage guide
- [ARCHITECTURE.md](ARCHITECTURE.md) - How AtlasForge works
- [README.md](README.md) - Project overview
