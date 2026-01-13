# AI-AtlasForge Installation Guide

This guide covers installing AI-AtlasForge on a fresh system.

## Prerequisites

### Required

- **Python 3.10+** - Check with `python3 --version`
- **pip** - Python package manager
- **Git** - For cloning the repository

### Optional

- **Node.js 18+** - For building dashboard JavaScript
- **Ollama** - For local LLM support
- **NVIDIA GPU + CUDA** - For GPU-accelerated workloads

### System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 8 GB | 16+ GB |
| Storage | 5 GB | 20+ GB |
| CPU | 4 cores | 8+ cores |

## Quick Install

The fastest way to get started:

```bash
# Clone the repository
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge

# Run the installer
./install.sh
```

The installer will:
1. Check prerequisites
2. Create a Python virtual environment
3. Install dependencies
4. Create required directories
5. Generate configuration files
6. Detect your hardware and create ENVIRONMENT.md
7. Optionally install systemd services

## Manual Installation

If you prefer to install manually:

### 1. Clone the Repository

```bash
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Install Node.js Dependencies (Optional)

If you want to modify the dashboard JavaScript:

```bash
npm install
node dashboard_static/build.js
```

### 5. Create Directories

```bash
mkdir -p state logs workspace/{artifacts,research,tests}
mkdir -p missions/mission_logs backups/auto_backups
mkdir -p atlasforge_data/{knowledge_base,analytics,exploration}
mkdir -p screenshots
```

### 6. Configure

```bash
# Create config from template
cp config.example.yaml config.yaml
cp .env.example .env

# Edit to add your API key
nano config.yaml  # or your preferred editor
```

### 7. Generate Environment Profile

```bash
python3 scripts/generate_environment.py
```

## Configuration

### API Key Setup

AI-AtlasForge requires an Anthropic API key. Get one at: https://console.anthropic.com/

Set your API key using one of these methods:

**Option 1: Environment Variable (Recommended)**
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

**Option 2: .env File**
```bash
echo "ANTHROPIC_API_KEY=your-api-key-here" >> .env
```

**Option 3: config.yaml**
```yaml
anthropic_api_key: "your-api-key-here"
```

### Configuration Options

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| Port | `ATLASFORGE_PORT` | 5050 | Dashboard port |
| Debug | `ATLASFORGE_DEBUG` | false | Enable debug mode |
| Root | `ATLASFORGE_ROOT` | (auto) | Installation directory |
| Display | `DISPLAY` | :99 | Virtual display for headless |

### Ollama Integration (Optional)

If you want to use a local LLM:

1. Install Ollama: https://ollama.ai/
2. Pull a model: `ollama pull llama3.1:8b`
3. Enable in config.yaml:
   ```yaml
   ollama:
     enabled: true
     url: "http://localhost:11434"
     model: "llama3.1:8b"
   ```

## systemd Services (Optional)

For auto-start on boot:

```bash
./scripts/setup_services.sh
```

This installs:
- `atlasforge-dashboard.service` - Dashboard web server
- `atlasforge-tray.service` - System tray indicator (desktop only)

### Service Commands

```bash
# Start/stop dashboard
sudo systemctl start atlasforge-dashboard
sudo systemctl stop atlasforge-dashboard

# View logs
sudo journalctl -u atlasforge-dashboard -f

# Enable/disable auto-start
sudo systemctl enable atlasforge-dashboard
sudo systemctl disable atlasforge-dashboard
```

## Verifying Installation

### 1. Start the Dashboard

```bash
source venv/bin/activate  # if using venv
python3 dashboard_v2.py
```

Open http://localhost:5050 in your browser.

### 2. Check the Dashboard

You should see:
- Mission status panel
- Activity feed
- Knowledge base widget
- Analytics widget

### 3. Create a Test Mission

1. Click "Create Mission" in the dashboard
2. Enter a simple objective (e.g., "Create a hello world Python script")
3. Set cycle budget to 1
4. Click "Create"

### 4. Run the Mission

In a new terminal:
```bash
source venv/bin/activate
python3 claude_autonomous.py --mode=rd
```

Watch the dashboard as Claude works through the stages.

## Troubleshooting

### "ModuleNotFoundError: No module named X"

Ensure you're using the virtual environment:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Dashboard won't start

Check if the port is in use:
```bash
lsof -i :5050
```

Use a different port:
```bash
ATLASFORGE_PORT=5051 python3 dashboard_v2.py
```

### "ANTHROPIC_API_KEY not set"

Ensure your API key is configured (see Configuration section above).

### Permission errors on Linux

Some operations require write access to project directories:
```bash
chmod -R u+w state logs workspace missions backups atlasforge_data
```

### systemd service fails

Check logs for details:
```bash
sudo journalctl -u atlasforge-dashboard -n 50
```

Common issues:
- Wrong Python path in service file
- Missing dependencies
- Incorrect working directory

### Node.js build errors

If `npm install` fails:
```bash
rm -rf node_modules package-lock.json
npm cache clean --force
npm install
```

## Upgrading

To upgrade to a new version:

```bash
cd AI-AtlasForge
git pull origin main
source venv/bin/activate
pip install -r requirements.txt

# If Node.js dependencies changed:
npm install
node dashboard_static/build.js
```

## Uninstalling

```bash
# Stop services
sudo systemctl stop atlasforge-dashboard atlasforge-tray
sudo systemctl disable atlasforge-dashboard atlasforge-tray

# Remove service files
sudo rm /etc/systemd/system/atlasforge-*.service
sudo systemctl daemon-reload

# Remove installation directory
rm -rf /path/to/AI-AtlasForge
```

## Getting Help

- **Documentation**: See USAGE.md for usage guide
- **Issues**: https://github.com/DragonShadows1978/AI-AtlasForge/issues
- **README**: Project overview and quick start
