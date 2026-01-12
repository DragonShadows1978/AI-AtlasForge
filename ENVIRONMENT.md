# System Environment Profile
*Auto-generated: 2025-12-03*

## Hardware Summary

| Component | Specification | Notes |
|-----------|--------------|-------|
| **GPU** | NVIDIA RTX 3070 | 8GB VRAM, Compute 8.6, 5888 CUDA cores |
| **CPU** | Intel i7-6700K | 4 cores / 8 threads @ 4.0-4.2GHz |
| **RAM** | 64GB DDR4 | ~30GB available |
| **Storage** | 500GB + 1TB SSD | 325GB free on primary |

## GPU Details
```
Model:          NVIDIA GeForce RTX 3070
VRAM:           8192 MiB (8GB)
Free VRAM:      ~7.5GB (when Ollama not loaded)
Compute Cap:    8.6 (Ampere architecture)
CUDA Cores:     5888
Tensor Cores:   184
```

**Utilization Opportunity:** GPU is currently underutilized. RL training runs on CPU (NumPy). Converting to PyTorch CUDA would yield 10-15x speedup.

## ML Stack
```
Python:         3.12.3
PyTorch:        2.9.0+cu128
CUDA:           12.8
cuDNN:          91002 (9.1.0)
```

All libraries are CUDA-ready. No installation needed.

## Running Services

| Service | Purpose | Resource Usage |
|---------|---------|----------------|
| **Ollama** | Local LLM (localhost:11434) | ~5.5GB VRAM when loaded |
| **Xvfb :99** | Virtual display for bot | Minimal |
| **x11vnc :99** | VNC for virtual display (port 5999) | Minimal |
| **x11vnc :0** | VNC for main display (port 5900) | Minimal |
| **Steam/Brotato** | Game target | ~1GB RAM |
| **Dashboards** | v2:5000, v3:5003 | Minimal |

## Ollama Models Available
```
llama3.1:8b           4.9GB   - Primary chat model
mistral:latest        4.4GB   - Alternative
mixtral:8x7b         26.4GB   - Large model (won't fit with other loads)
mm-* variants         4.9GB   - Custom mini-mind models
```

## Network
- Local IP: 192.168.1.70
- Tailscale: 100.127.159.40
- Key ports: 5000 (v2 dash), 5003 (v3 dash), 5999 (VNC :99), 11434 (Ollama)

## Virtual Display
```
Display:    :99
Resolution: 1920x1080x24
VNC Port:   5999
```
All game/bot operations should use `DISPLAY=:99`

## Optimization Opportunities

### Immediate (High Impact)
1. **CUDA for RL Policy** - RTX 3070 sitting idle during training
   - Current: 6.6ms inference (CPU NumPy)
   - Potential: 0.5ms inference (CUDA PyTorch) = 12x faster
   - Training batches: 50x faster with GPU parallelism

### Medium Term
2. **Batch Processing** - GPU can process 64-128 samples simultaneously
3. **Mixed Precision** - RTX 3070 has Tensor cores for FP16, could 2x throughput
4. **Multi-process Data Loading** - 8 threads available for parallel data prep

### Resource Headroom
- GPU: 7.5GB free (Ollama unloaded) or 2GB free (Ollama loaded)
- CPU: Generally 50-70% idle during bot operation
- RAM: 30GB available
- Disk: 325GB free

## Commands for Monitoring
```bash
# GPU status
nvidia-smi

# GPU live monitoring
watch -n1 nvidia-smi

# CPU/Memory
htop

# Disk
df -h

# Ollama status
curl http://localhost:11434/api/tags
```
