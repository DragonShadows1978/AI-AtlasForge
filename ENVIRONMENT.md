# Environment Profile

Updated: 2026-06-13
Hostname: MilleniumFalcon

This file documents the hardware profile of this installation.
The autonomous agent uses this information for resource-aware planning.

## System Overview

| Component | Details |
|-----------|---------|
| Hostname | MilleniumFalcon |
| OS | Linux Mint 22.3 (Ubuntu-derived) |
| Kernel | 6.17.0-35-generic |
| Architecture | x86_64 |
| Python | 3.12.3 |

## CPU

| Property | Value |
|----------|-------|
| Model | AMD Ryzen 5 3600 6-Core Processor |
| Cores | 6 |
| Threads | 12 |
| Base Clock | 3.6 GHz (boost to 4.2 GHz) |

## Memory

| Property | Value |
|----------|-------|
| Total RAM | 64 GB |
| Swap | 2 GB |

## GPU

| Property | Value |
|----------|-------|
| Model | NVIDIA GeForce RTX 4070 SUPER |
| Architecture | AD104 (Ada Lovelace) |
| VRAM Total | 12,282 MB (12 GB) |
| CUDA Available | Yes |
| CUDA Version | 13.2 (runtime) / 12.0 (nvcc toolkit) |
| NVIDIA Driver | 595.71.05 |
| CuPy Version | 14.0.1 |
| PyTorch CUDA | Available (2.11.0+cu130) |

## Storage

| Path | Device | Total | Available | Purpose |
|------|--------|-------|-----------|---------|
| / | /dev/sda2 (SSD) | 916 GB | 631 GB | System, AI-AtlasForge (symlinked) |
| /mnt/ForgeRealm | /dev/nvme0n1p1 (NVMe) | 916 GB | 709 GB | Primary project storage, workspaces, missions |
| /mnt/Shared | //192.168.1.70/Shared (CIFS) | 457 GB | 137 GB | Network share (NAS), cross-device file exchange |

## Services

| Service | Status | Purpose |
|---------|--------|---------|
| atlasforge.service | Running | Dashboard (port 5010, HTTPS) |
| atlasforge-web-proxy.service | Running | Web search proxy (port 8765, threaded) |
| atlasforge-tray.service | Inactive (enabled) | System tray indicator |
| email-investigation.service | Running | Email investigation engine |
| email-investigation-tray.service | Running | Email investigation tray |
| openclaw-gateway.service | Running | OpenClaw gateway (v2026.5.7) |
| Ollama | Available (v0.17.5) | Local LLM serving |

## Resource Limits

| Setting | Value | File |
|---------|-------|------|
| AtlasForge MemoryHigh | 48 GB | ~/.config/systemd/user/atlasforge.service.d/10-resource-guard.conf |
| AtlasForge MemoryMax | 56 GB | ~/.config/systemd/user/atlasforge.service.d/10-resource-guard.conf |
| AtlasForge CPUQuota | 400% | ~/.config/systemd/user/atlasforge.service.d/10-resource-guard.conf |

## Key Software

| Package | Version | Purpose |
|---------|---------|---------|
| tensor_gpu_v2 | Custom | CuPy-based tensor framework with APA-Quant, FlashAttention, TurboQuant |
| CuPy | 14.0.1 | CUDA array library |
| PyTorch | 2.11.0+cu130 | ML framework (used for model loading, not for APA runtime) |
| transformers | 5.12.0 | HuggingFace model loading |
| esbuild | 0.27.2 | Dashboard JS bundling |

## Key Directories

| Path | Purpose |
|------|---------|
| /home/vader/AI-AtlasForge | AtlasForge codebase (symlinked to ForgeRealm) |
| /mnt/ForgeRealm/AI-AtlasForge | AtlasForge primary storage |
| /mnt/ForgeRealm/Project-Tensor | tensor_gpu_v2 framework (APA-Quant lives here) |
| /mnt/ForgeRealm/AI-AfterImage | AfterImage knowledge base |
| /mnt/ForgeRealm/collatz-experimental-data | Collatz Lock 2/4 computational scans |
| /home/vader/AI-AtlasForge/state | Mission state, conductor locks |
| /home/vader/AI-AtlasForge/logs | Conductor logs |

## Resource Recommendations

Based on detected hardware:

- **GPU:** RTX 4070 SUPER with 12GB VRAM. Supports INT4 quantized models up to ~13B parameters (e.g. the Gemma 4 12B pure-KV port runs on this hardware). APA-Quant extends effective context 4-5x.
- **Multi-core CPU:** 12 threads available for parallel workloads.
- **64 GB RAM:** Sufficient for large mission workloads. AtlasForge cgroup set to 48GB high watermark.
- **NVMe storage:** ForgeRealm mount provides fast workspace I/O for mission artifacts and model weights.
- **Network share:** /mnt/Shared for cross-device file exchange (NAS at 192.168.1.70).

## Notes

- This file was last manually verified on 2026-06-13
- GPU upgraded RTX 3070 (8 GB, GA104) → RTX 4070 SUPER (12 GB, AD104); driver 595.58.03 → 595.71.05, CUDA runtime 13.0 → 13.2
- OS bumped to Linux Mint 22.3; kernel 6.17.0-23 → 6.17.0-35-generic
- collatz-lock2-amax50.service retired (scan complete); atlasforge-tray.service is enabled but currently inactive
- Earlier auto-generated versions contained stale hardware from a prior machine (i7-6700K / GTX 1660 SUPER) — corrected on 2026-05-22
- GPU VRAM availability varies at runtime depending on active missions and model loads
- The web proxy runs in threaded mode (Flask threaded=True) to handle concurrent subagent searches
