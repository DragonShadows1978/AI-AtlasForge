# AI-AtlasForge Makefile
# https://github.com/DragonShadows1978/AI-AtlasForge
#
# Run 'make help' to see available targets

# Colors for terminal output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m  # No Color

# Paths
ATLASFORGE_ROOT := $(shell pwd)
VENV := $(ATLASFORGE_ROOT)/venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

# Default target
.DEFAULT_GOAL := help

.PHONY: help install dev verify dashboard run stop clean docker docker-down sample-mission test lint check-api

##@ Getting Started

help: ## Show this help message
	@echo ""
	@echo "$(BLUE)AI-AtlasForge$(NC) - Autonomous AI R&D Platform"
	@echo ""
	@echo "$(YELLOW)Usage:$(NC) make [target]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf ""} /^[a-zA-Z_-]+:.*?##/ { printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(YELLOW)%s$(NC)\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""

install: ## Run full installation (creates venv + installs dependencies)
	@echo "$(BLUE)[AtlasForge]$(NC) Running installation..."
	@./install.sh
	@echo ""
	@echo "$(GREEN)Installation complete!$(NC) Run 'make verify' to check your setup."

quick-install: ## One-liner install (same as ./install.sh)
	@./install.sh

##@ Development

dev: ## Install with development dependencies
	@echo "$(BLUE)[AtlasForge]$(NC) Setting up development environment..."
	@if [ ! -d "$(VENV)" ]; then python3 -m venv $(VENV); fi
	@$(PIP) install --upgrade pip
	@$(PIP) install -r requirements.txt
	@if [ -f requirements-dev.txt ]; then $(PIP) install -r requirements-dev.txt; fi
	@echo "$(GREEN)Development environment ready!$(NC)"

lint: ## Run code linting (if configured)
	@if [ -f "$(VENV)/bin/ruff" ]; then \
		$(VENV)/bin/ruff check .; \
	elif [ -f "$(VENV)/bin/flake8" ]; then \
		$(VENV)/bin/flake8 .; \
	else \
		echo "$(YELLOW)No linter installed. Run: pip install ruff$(NC)"; \
	fi

test: ## Run tests
	@if [ -d "tests" ]; then \
		$(PYTHON) -m pytest tests/ -v; \
	else \
		echo "$(YELLOW)No tests directory found$(NC)"; \
	fi

##@ Running AtlasForge

dashboard: ## Start the dashboard server (http://localhost:5050)
	@echo "$(BLUE)[AtlasForge]$(NC) Starting dashboard..."
	@if [ -f "$(VENV)/bin/activate" ]; then \
		. $(VENV)/bin/activate && python3 dashboard_v2.py; \
	else \
		python3 dashboard_v2.py; \
	fi

run: ## Start the autonomous agent in R&D mode
	@echo "$(BLUE)[AtlasForge]$(NC) Starting autonomous agent..."
	@if [ -f "$(VENV)/bin/activate" ]; then \
		. $(VENV)/bin/activate && python3 atlasforge_conductor.py --mode=rd; \
	else \
		python3 atlasforge_conductor.py --mode=rd; \
	fi

run-free: ## Start the autonomous agent in free exploration mode
	@echo "$(BLUE)[AtlasForge]$(NC) Starting agent in free mode..."
	@if [ -f "$(VENV)/bin/activate" ]; then \
		. $(VENV)/bin/activate && python3 atlasforge_conductor.py --mode=free; \
	else \
		python3 atlasforge_conductor.py --mode=free; \
	fi

stop: ## Stop all AtlasForge processes
	@echo "$(BLUE)[AtlasForge]$(NC) Stopping all processes..."
	@pkill -f "python3 dashboard_v2.py" 2>/dev/null || true
	@pkill -f "python3 atlasforge_conductor.py" 2>/dev/null || true
	@echo "$(GREEN)All processes stopped$(NC)"

##@ Docker

docker: ## Start with Docker Compose
	@echo "$(BLUE)[AtlasForge]$(NC) Starting Docker containers..."
	@docker compose up -d
	@echo ""
	@echo "$(GREEN)Dashboard running at:$(NC) http://localhost:5050"

docker-down: ## Stop Docker containers
	@echo "$(BLUE)[AtlasForge]$(NC) Stopping Docker containers..."
	@docker compose down

docker-logs: ## Show Docker container logs
	@docker compose logs -f

docker-build: ## Rebuild Docker image
	@docker compose build --no-cache

##@ Verification

verify: ## Verify installation is complete and working
	@./verify.sh

check-api: ## Check if Anthropic API key is configured
	@echo "$(BLUE)[AtlasForge]$(NC) Checking API configuration..."
	@if [ -n "$$ANTHROPIC_API_KEY" ]; then \
		echo "$(GREEN)[OK]$(NC) ANTHROPIC_API_KEY is set in environment"; \
	elif grep -q "ANTHROPIC_API_KEY" .env 2>/dev/null; then \
		echo "$(GREEN)[OK]$(NC) ANTHROPIC_API_KEY found in .env"; \
	elif grep -q "anthropic_api_key" config.yaml 2>/dev/null; then \
		echo "$(GREEN)[OK]$(NC) anthropic_api_key found in config.yaml"; \
	else \
		echo "$(RED)[ERROR]$(NC) No API key found!"; \
		echo "  Set ANTHROPIC_API_KEY in environment, .env, or config.yaml"; \
		exit 1; \
	fi

##@ Sample Mission

sample-mission: ## Load the hello-world sample mission
	@echo "$(BLUE)[AtlasForge]$(NC) Loading sample mission..."
	@if [ -f "$(VENV)/bin/activate" ]; then \
		. $(VENV)/bin/activate && python3 scripts/load_sample_mission.py; \
	else \
		python3 scripts/load_sample_mission.py; \
	fi
	@echo ""
	@echo "$(GREEN)Sample mission loaded!$(NC) Run 'make run' to start it."

##@ Maintenance

clean: ## Clean generated files and caches
	@echo "$(BLUE)[AtlasForge]$(NC) Cleaning generated files..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name ".DS_Store" -delete 2>/dev/null || true
	@rm -rf .pytest_cache 2>/dev/null || true
	@rm -rf .ruff_cache 2>/dev/null || true
	@echo "$(GREEN)Cleanup complete$(NC)"

clean-all: ## Clean everything including venv (destructive!)
	@echo "$(RED)WARNING: This will delete the virtual environment!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	@$(MAKE) clean
	@rm -rf $(VENV)
	@echo "$(GREEN)Full cleanup complete. Run 'make install' to reinstall.$(NC)"

reset-state: ## Reset mission state (clears current mission)
	@echo "$(YELLOW)WARNING: This will clear the current mission state!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	@rm -f state/mission.json state/claude_state.json
	@echo "$(GREEN)State reset complete$(NC)"
