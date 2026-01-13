# Environment Profile (Template)

This is a template for the auto-generated ENVIRONMENT.md file.
Run `python3 scripts/generate_environment.py` to generate the actual profile.

## System Overview

| Component | Details |
|-----------|---------|
| OS | Linux 6.x |
| Architecture | x86_64 |
| Python | 3.10+ |

## CPU

| Property | Value |
|----------|-------|
| Model | (Detected at install) |
| Cores | (Detected at install) |
| Threads | (Detected at install) |

## Memory

| Property | Value |
|----------|-------|
| Total RAM | (Detected at install) |
| Available | (Detected at install) |
| Swap | (Detected at install) |

## GPU

| Property | Value |
|----------|-------|
| Available | Yes/No |
| Model | (Detected at install) |
| VRAM Total | (Detected at install) |
| VRAM Free | (Detected at install) |
| CUDA Available | Yes/No |
| CUDA Version | (Detected at install) |

## Storage

| Path | Total | Available |
|------|-------|-----------|
| / | (Detected at install) | (Detected at install) |

## Services

| Service | Status |
|---------|--------|
| Ollama | Running/Not running |
| Docker | Available/Not available |

## Resource Recommendations

The generated ENVIRONMENT.md will include recommendations based on detected hardware:

- GPU availability and VRAM for ML workloads
- CPU core count for multiprocessing decisions
- RAM availability for caching strategies
- Running services (Ollama, Docker) for integration options

## Generating This File

```bash
python3 scripts/generate_environment.py
```

This will detect your hardware and create ENVIRONMENT.md with actual values.

## Why This Matters

The autonomous agent reads ENVIRONMENT.md during planning to make resource-aware decisions:

- **GPU detected**: Use CUDA acceleration where possible
- **High RAM**: Cache more data in memory
- **Many cores**: Use multiprocessing for parallel tasks
- **Ollama running**: Local LLM available for testing

Without this file, the agent may make suboptimal resource choices or miss available hardware accelerators.
