# AI-AtlasForge Docker Image
# Multi-stage build for optimized image size

# ============================================
# Stage 1: Build stage
# ============================================
FROM python:3.11-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for layer caching
COPY requirements.txt /tmp/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# ============================================
# Stage 2: Production stage
# ============================================
FROM python:3.11-slim-bookworm AS production

# Set labels
LABEL maintainer="AtlasForge Team"
LABEL version="1.0.0"
LABEL description="AI-AtlasForge - Autonomous AI Research & Development Platform"

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash atlasforge

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=atlasforge:atlasforge . /app/

# Create required directories with proper permissions
RUN mkdir -p /app/state /app/missions /app/logs /app/workspace /app/knowledge_base \
    && chown -R atlasforge:atlasforge /app/state /app/missions /app/logs /app/workspace /app/knowledge_base

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ATLASFORGE_ROOT=/app
ENV ATLASFORGE_PORT=5050
ENV ATLASFORGE_HOST=0.0.0.0

# Switch to non-root user
USER atlasforge

# Expose dashboard port
EXPOSE 5050

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${ATLASFORGE_PORT}/health || exit 1

# Default command - run dashboard
CMD ["python", "dashboard_v2.py"]
