# GPU Profiling Tools: Comprehensive Comparison Matrix

## Tool Overview

| Tool | Vendor | Architecture | Focus | Overhead | Learning Curve |
|------|--------|--------------|-------|----------|-----------------|
| **Nsight Compute** | NVIDIA | NVIDIA GPU | Kernel analysis | High (5-50x) | Moderate |
| **Nsight Systems** | NVIDIA | NVIDIA GPU | System timeline | Low (1-5x) | Moderate |
| **PyTorch Profiler** | Meta | NVIDIA/CPU | Model layers | Low (1-3x) | Low |
| **TensorFlow Profiler** | Google | All | Model layers | Low-Moderate | Low |
| **Omniperf** | AMD | RDNA/CDNA | Kernel analysis | Moderate | Moderate |
| **rocprof** | AMD | RDNA/CDNA | Low-level trace | Moderate | High |
| **nvidia-smi** | NVIDIA | NVIDIA GPU | Real-time monitor | Minimal | Very Low |
| **PyTorch utils.bottleneck** | Meta | NVIDIA/CPU | Quick overview | Low | Very Low |

---

## Detailed Feature Comparison

### Metric Collection Capabilities

| Metric | Nsight Comp | Nsight Sys | PyTorch | TensorFlow | Omniperf | rocprof |
|--------|-------------|-----------|---------|-----------|----------|---------|
| SM Utilization | ✓✓✓ | ✓ | △ | △ | ✓✓✓ | ✓✓ |
| Memory Bandwidth | ✓✓✓ | ✓ | ◊ | ◊ | ✓✓✓ | ✓✓ |
| Cache Hit Rates | ✓✓✓ | ✗ | ✗ | ✗ | ✓✓✓ | ✓ |
| Occupancy | ✓✓✓ | ◊ | ◊ | ◊ | ✓✓ | ✓ |
| Warp Efficiency | ✓✓ | ✗ | ✗ | ✗ | ✓ | ✓ |
| Memory Latency | ✓ | ◊ | ◊ | ◊ | ✓ | ✓ |
| Power/Energy | △ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Kernel Timeline | ✗ | ✓✓✓ | ✓ | ✓ | ✓ | ✓ |
| Memory Access Pattern | ✓ | △ | ✗ | ✗ | ✓ | △ |
| Instruction Breakdown | ✓✓ | ✗ | ✗ | ✗ | ✓✓ | ✓ |

**Legend:** ✓✓✓ = Excellent, ✓✓ = Good, ✓ = Available, △ = Limited, ◊ = Estimated, ✗ = Not available

---

## Use Case Recommendation Matrix

### Which tool for which task?

| Task | Best Tool | Alternative | Why |
|------|-----------|-------------|-----|
| Quick model profiling | PyTorch Profiler | utils.bottleneck | Low overhead, layer breakdown |
| Deep kernel analysis | Nsight Compute | Omniperf (AMD) | Detailed metrics, roofline |
| System-level timeline | Nsight Systems | rocprof | Low overhead, comprehensive view |
| Memory bottleneck ID | Nsight Compute | TensorFlow Profiler | Cache hierarchy analysis |
| Real-time monitoring | nvidia-smi | PyTorch monitor | Minimal overhead |
| Custom CUDA kernel | Nsight Compute | rocprof | Kernel-level metrics |
| Production workload | Nsight Systems | PyTorch lightweight | Lower overhead |
| CI/CD regression | PyTorch Profiler | Nsight Systems | Programmatic, lightweight |
| Teaching/Learning | PyTorch Profiler | utils.bottleneck | Simple to understand |
| Large-scale clusters | Nsight Systems | TensorFlow | Multi-GPU support |

---

## Installation and Setup

### NVIDIA Tools

```bash
# Nsight Compute (recommended)
# Option 1: CUDA Toolkit
sudo apt-get install nvidia-cuda-toolkit

# Option 2: Standalone download
# https://developer.nvidia.com/nsight-compute
# Extract and add to PATH:
export PATH=/path/to/nsight-compute/bin:$PATH

# Verify
ncu --version
```

### AMD Tools

```bash
# Omniperf
sudo apt-get install omniperf  # Ubuntu/Debian
# Or build from source
git clone https://github.com/ROCm/omniperf.git
cd omniperf
mkdir build && cd build
cmake ..
make -j$(nproc)
sudo make install

# rocprof (included with ROCm)
export PATH=/opt/rocm/bin:$PATH
rocprof --version
```

### Python Tools

```bash
pip install torch tensorflow torch-profiler
```

---

## Performance Characteristic Comparison

### Profiling Speed

```
Tool                    Time for 100ms Workload    Overhead
──────────────────────────────────────────────────────────
nvidia-smi              <10ms                      <5%
PyTorch Profiler        100-500ms                  1-3x
Nsight Systems          500ms-1s                   2-5x
Nsight Compute          5-30s                      10-50x
Omniperf                5-20s                      10-30x
rocprof                 2-10s                      10-20x
TensorFlow Profiler     100-500ms                  1-3x
```

### Memory Overhead

```
Tool                    Memory for Report
──────────────────────────────────────────
PyTorch Profiler        50-200 MB
Nsight Systems          100-500 MB
Nsight Compute          200-1000 MB
Omniperf                100-400 MB
TensorFlow Profiler     50-150 MB
rocprof                 100-300 MB
```

---

## Output Format Comparison

| Tool | Formats | GUI | Programmatic Access | Export |
|------|---------|-----|-------------------|--------|
| Nsight Compute | .ncu-rep | ncu-ui (native) | Python API (limited) | CSV, JSON |
| Nsight Systems | .nsys-rep | nsys-ui (native) | Python API (limited) | CSV, SQLite |
| PyTorch | JSON, PT | TensorBoard | Python (direct) | Chrome trace |
| TensorFlow | Events, Trace | TensorBoard | Protobuf | Protobuf |
| Omniperf | JSON, CSV | Web UI | Python (limited) | JSON, CSV |
| rocprof | CSV, JSON | Web UI | CLI parsing | CSV, JSON |

---

## Bottleneck Detection Capability

### How each tool identifies bottlenecks

| Tool | Method | Accuracy | Explanation |
|------|--------|----------|-------------|
| **Nsight Compute** | Multi-metric thresholding | 85-95% | Combined SM, memory, occupancy analysis |
| **Roofline (manual)** | Arithmetic intensity | 80-90% | Depends on accurate FLOPS/memory measurement |
| **PyTorch Profiler** | Kernel time analysis | 70-80% | Works well for layer comparison, not kernel internals |
| **Omniperf** | Counter-based heuristics | 85-90% | Similar to Nsight but AMD-specific |
| **Manual inspection** | Expert analysis | 90%+ | Requires deep GPU architecture knowledge |

---

## Example Workflow Comparison

### Scenario: Optimize a PyTorch Model

**Quick approach (5 minutes):**
```bash
python -m torch.utils.bottleneck model.py
# → Identifies slow layers

# Profile slow layer
python -c "
import torch
from torch.profiler import profile, ProfilerActivity

# Isolated layer profiling
with profile(activities=[ProfilerActivity.CUDA]) as p:
    layer(input_data)
print(p.key_averages().table(sort_by='cuda_time_total'))
"
```

**Thorough approach (30 minutes):**
```bash
# 1. Layer-level profiling with PyTorch
ncu -o baseline.ncu-rep python model.py

# 2. System timeline
nsys profile -o timeline.nsys-rep python model.py

# 3. Deep kernel analysis
ncu -o kernel_detail.ncu-rep --kernel-name "kernel_regex" python model.py

# 4. Export and analyze
ncu --export report.csv -i kernel_detail.ncu-rep
python analyze_report.py report.csv
```

**Production approach (2+ hours):**
```bash
# 1. Multi-run baseline collection
for i in {1..5}; do
    ncu -o run_$i.ncu-rep python model.py
done

# 2. Comparative analysis
for i in {1..5}; do
    ncu --export run_$i.csv -i run_$i.ncu-rep
done
python comparative_analysis.py run_*.csv

# 3. Roofline analysis
ncu --set roofline -o roofline.ncu-rep python model.py
ncu --export roofline.csv -i roofline.ncu-rep
python plot_roofline.py roofline.csv
```

---

## Selection Criteria Decision Tree

```
Start: Need to profile GPU code
│
├─ Q1: What's your time budget?
│  ├─ < 5 min → PyTorch Profiler or utils.bottleneck
│  ├─ 5-20 min → Nsight Systems
│  └─ 20+ min → Nsight Compute + Omniperf
│
├─ Q2: What GPU architecture?
│  ├─ NVIDIA → Nsight tools (best support)
│  ├─ AMD → Omniperf or rocprof
│  └─ Multi-vendor → PyTorch Profiler
│
├─ Q3: What level of detail needed?
│  ├─ Layer/operation level → PyTorch Profiler
│  ├─ Kernel level → Nsight Compute
│  └─ System level → Nsight Systems
│
└─ Q4: What's your expertise?
   ├─ Beginner → utils.bottleneck
   ├─ Intermediate → PyTorch Profiler
   └─ Advanced → Nsight Compute
```

---

## Common Pitfalls and Solutions

### Pitfall 1: Profiler Overhead Skews Results

**Problem:** Profiler adds 10-50x overhead
**Solution:** 
- Use lightweight profiler first (PyTorch)
- Extend workload to smooth out overhead
- Profile in batches to amortize overhead

### Pitfall 2: Misinterpreting Metrics

**Problem:** High SM utilization != good performance
**Solution:**
- Cross-reference multiple metrics
- Use roofline analysis for context
- Compare with theoretical peak

### Pitfall 3: Single-Run Results are Unstable

**Problem:** Thermal throttling, system noise cause variance
**Solution:**
- Collect multiple runs (5-10)
- Cool GPU between runs
- Use median instead of mean

### Pitfall 4: Kernel Fusion Hides Bottlenecks

**Problem:** Can't see individual kernel performance
**Solution:**
- Disable kernel fusion: `DISABLE_KERNEL_FUSION=1`
- Use Nsight Systems for timeline view

### Pitfall 5: Memory Profiling Overhead is Extreme

**Problem:** `profile_memory=True` adds 10x+ overhead
**Solution:**
- Use separate memory profile run
- Profile in isolation from compute profiling
- Use Nsight Systems for memory timeline instead

---

## Quick Reference Cheat Sheet

### Installation (one-liner per tool)

```bash
# NVIDIA
pip install torch && sudo apt-get install nvidia-cuda-toolkit

# AMD
curl https://repo.radeon.com/rocm/rocm.gpg.key | sudo apt-key add -
sudo apt-get install omniperf

# Python tools
pip install torch tensorflow torch-profiler
```

### Basic Commands

```bash
# PyTorch (fastest)
python -m torch.utils.bottleneck script.py

# PyTorch Profiler (flexible)
python -c "from torch.profiler import *; profile(script.py)"

# Nsight Systems (system view)
nsys profile -o report.nsys-rep python script.py

# Nsight Compute (deep analysis)
ncu -o report.ncu-rep python script.py

# AMD Omniperf
omniperf profile -n test -- ./app

# View reports
ncu-ui report.ncu-rep           # Nsight Compute GUI
nsys-ui report.nsys-rep         # Nsight Systems GUI
tensorboard --logdir=logs       # TensorFlow
```

### Key Metrics to Check

```
Metric                          Target      Issue if
─────────────────────────────────────────────────────
SM Utilization                  >70%        <50% = underutilized
Memory Bandwidth Utilization    70-90%      >80% = memory-bound
Achieved Occupancy              >50%        <30% = register pressure
Warp Efficiency                 >90%        <70% = divergence
Arithmetic Intensity vs Ridge   >ridge      <ridge = memory-bound
Cache Hit Rate                  >90%        <60% = cache miss problem
Branch Efficiency               >95%        <90% = branch divergence
```
