# GPU Bottleneck Identification: Reference Card

**One-page quick reference for GPU profiling workflows**

---

## TL;DR: 5-Minute Profiling

```bash
# Choose your tool based on time budget and detail level:

# Fastest (2-3 minutes):
python -m torch.utils.bottleneck your_script.py

# Standard (5-10 minutes):
nsys profile -o report.nsys-rep python your_script.py
nsys stats -r cuda_gpu_kern_sum report.nsys-rep

# Detailed (10-30 seconds per kernel):
ncu -o report.ncu-rep python your_script.py
ncu --export csv -i report.ncu-rep
python analyze_bottleneck.py report.csv
```

---

## Bottleneck Classification Chart

```
┌─ Measure Metrics ─────────────────────────────────────────┐
│ SM Utilization, Memory Bandwidth, Occupancy               │
└───────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ Is Memory Bandwidth > 80% AND SM Util < 50%?               │
│ ├─ YES → MEMORY-BOUND                                      │
│ │  ├─ Symptoms: High memory traffic, low SM activity       │
│ │  └─ Fix: Coalesce memory, use shared mem, tile loops     │
│ │                                                          │
│ └─ NO: Is Occupancy > 70% AND SM Util > 80%?              │
│    ├─ YES → COMPUTE-BOUND                                 │
│    │  ├─ Symptoms: High computation, memory not limiting   │
│    │  └─ Fix: Use tensor cores, increase ILP, TF32         │
│    │                                                       │
│    └─ NO: Is Occupancy < 30%?                             │
│       ├─ YES → REGISTER PRESSURE                          │
│       │  ├─ Symptoms: Low active warps per block           │
│       │  └─ Fix: Reduce registers, simplify kernel         │
│       │                                                    │
│       └─ Check: Is Warp Efficiency < 70%?                │
│          ├─ YES → WARP DIVERGENCE                         │
│          │  └─ Fix: Restructure branches, use predication │
│          │                                                │
│          └─ NO → BALANCED (check kernel-specific issues)  │
└─────────────────────────────────────────────────────────────┘
```

---

## Metric Interpretation Cheat Sheet

### SM Utilization
```
< 20%  ⚠ Severely underutilized - massive opportunity
20-50% ⚠ Underutilized - increase thread/block size
50-70% ✓ Good - acceptable utilization
70-90% ✓✓ Excellent - high utilization
> 90%  ✓✓✓ Maxed out - already optimized
```

### Memory Bandwidth Utilization
```
< 30%  ✓ Good - memory not the bottleneck
30-70% ✓ Acceptable - balanced usage
70-85% ✓ Good utilization - approaching limit
85-95% ⚠ Very high - likely memory-bound
> 95%  ⚠⚠ Maxed out - severe memory bottleneck
```

### Achieved Occupancy
```
< 10%  ⚠⚠⚠ Critical - severe register pressure
10-30% ⚠⚠ Poor - significant pressure
30-50% ⚠ Moderate - could improve
50-70% ✓ Good - acceptable
70-100% ✓✓ Excellent - high occupancy
```

### Warp Efficiency
```
< 50%  ⚠⚠ Severe divergence
50-70% ⚠ Significant divergence
70-90% ✓ Moderate - acceptable
90-100% ✓✓ Excellent - minimal divergence
```

### Arithmetic Intensity (vs Ridge Point)
```
<ridge/4    ⚠⚠ Severely memory-bound
ridge/4-3/4 ⚠  Memory-bound
3/4-1.2×    ✓  Balanced
1.2×-3×     ✓✓ Compute-bound
>3×ridge    ⚠⚠ Likely compute issue or measurement error
```

---

## Tool Quick Selection

| Profile type | Time | Tool | Command |
|---|---|---|---|
| Layer-level | 2 min | PyTorch | `python -m torch.utils.bottleneck` |
| Quick check | 3-5 min | PyTorch Profiler | `torch.profiler.profile()` |
| System view | 5-10 min | Nsight Sys | `nsys profile` |
| Kernel detail | 10-30 min | Nsight Comp | `ncu` |
| AMD GPU | 5-20 min | Omniperf | `omniperf profile` |
| Real-time | Continuous | nvidia-smi | `nvidia-smi dmon` |

---

## Common Command Patterns

### Profile & Export
```bash
# Nsight Compute
ncu -o profile.ncu-rep python script.py
ncu --export csv -i profile.ncu-rep > metrics.csv

# Nsight Systems  
nsys profile -o profile.nsys-rep python script.py
nsys stats -r cuda_gpu_kern_sum profile.nsys-rep

# PyTorch
python -c "
import torch
from torch.profiler import profile, ProfilerActivity
with profile(activities=[ProfilerActivity.CUDA]) as p:
    model(x)
print(p.key_averages().table(sort_by='cuda_time_total'))
"
```

### Memory Analysis
```bash
# Profile memory
ncu --set roofline -o profile.ncu-rep python script.py

# PyTorch memory
python -c "
with profile(activities=[ProfilerActivity.CUDA], profile_memory=True) as p:
    model(x)
print(p.key_averages().table(sort_by='self_cuda_memory_usage'))
"
```

### Automated Bottleneck Detection
```python
def classify_bottleneck(sm_util, mem_bw, occupancy, warp_eff):
    if mem_bw > 80 and sm_util < 50:
        return "MEMORY_BOUND"
    if sm_util > 80 and occupancy > 70:
        return "COMPUTE_BOUND"
    if occupancy < 30:
        return "REGISTER_PRESSURE"
    if warp_eff < 70:
        return "WARP_DIVERGENCE"
    return "BALANCED"
```

---

## Roofline Model Quick Reference

### Ridge Points (FP32, example GPUs)
```
GPU             Peak TFLOPS    Memory BW (TB/s)    Ridge Point
─────────────────────────────────────────────────────────
A100 SXM        312.0          2.0                 156,000
A100 PCIe       312.0          2.0                 156,000
A10 Tensor      250.0          0.94                265,000
V100 SXM        112.0          0.9                 124,000
T4              65.0           0.32                203,000
RTX 3090        39.3           0.94                41,800
RTX 4090        82.6           1.06                77,900
```

**How to use:** If arithmetic_intensity < ridge_point → memory-bound

---

## Optimization Checklist

### Memory-Bound Kernels (high mem BW, low compute)
```
□ Check memory access coalescing
□ Enable L1 cache for reads if applicable
□ Increase data reuse with shared memory
□ Use memory-efficient data types (fp16, int8)
□ Consider loop fusion to reduce memory traffic
□ Profile with --roofline to confirm
□ Measure arithmetic intensity
```

### Compute-Bound Kernels (high compute, high SM util)
```
□ Use tensor cores (FP32→TF32, FP64→TF32)
□ Increase instruction-level parallelism
□ Reduce instruction latency (pipelining)
□ Avoid expensive operations (div, sin, sqrt)
□ Use appropriate math intrinsics
□ Profile with --set full for instruction breakdown
```

### Register Pressure (low occupancy)
```
□ Reduce registers per thread
□ Simplify kernel logic
□ Reduce loop unrolling
□ Decrease block size
□ Check with --set=full for register usage
□ Profile occupancy vs block size tradeoff
```

### Warp Divergence (low warp efficiency)
```
□ Align branch decisions to warps
□ Use predication instead of branching
□ Restructure conditionals for coalescing
□ Sort data to reduce branching
□ Profile with --set full for branch metrics
```

---

## Diagnosis Flowchart

```
Start with profiling result
    ↓
Does kernel run at all?
├─ NO → Check for errors, crashes
│       Run with error checking on
└─ YES ↓
        Extract metrics from report
        ↓
        Memory BW > 80%?
        ├─ YES → LIKELY MEMORY-BOUND
        │        ├─ Check SM Util < 50%? (confirms)
        │        ├─ Check arithmetic intensity
        │        └─ Apply memory optimizations
        │
        └─ NO → SM Util > 80%?
                ├─ YES → LIKELY COMPUTE-BOUND
                │        ├─ Check occupancy > 50%? (confirms)
                │        └─ Apply compute optimizations
                │
                └─ NO → Occupancy < 30%?
                        ├─ YES → REGISTER PRESSURE
                        │        └─ Reduce regs/thread
                        │
                        └─ NO → Warp Eff < 70%?
                                ├─ YES → WARP DIVERGENCE
                                │        └─ Fix branches
                                │
                                └─ NO → BALANCED
                                        Check specific kernel
                                        ops with Nsight detail
```

---

## Common Pitfalls & Quick Fixes

| Problem | Symptom | Quick Fix |
|---------|---------|-----------|
| Profiler overhead | Results inconsistent, too slow | Use lightweight tool (PyTorch) |
| Thermal throttling | Performance varies run-to-run | Cool GPU, run at idle, average runs |
| Kernel fusion | Can't see individual kernels | Set `DISABLE_KERNEL_FUSION=1` |
| Misaligned metrics | SM util high but performance low | Cross-check with memory/occupancy |
| Small workload | Profiler overhead dominates | Increase problem size or loop count |
| Synchronization hidden | Metrics look good but slow | Use Nsight Systems timeline view |
| Wrong block size | Occupancy drops with bigger blocks | Indicates register pressure |
| Memory stalls | High memory BW but compute low | Check cache hit rates, latency |

---

## One-Command Recipes

### Profile and auto-classify
```bash
python3 << 'EOF'
import subprocess, json
result = subprocess.run(
    ['ncu', '--export', 'json', '-i', 'report.ncu-rep'],
    capture_output=True, text=True
)
# Parse JSON and classify based on metrics
EOF
```

### Monitor in real-time
```bash
watch -n 1 'nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory,memory.used \
    --format=csv,noheader'
```

### Profile PyTorch model
```bash
python -c "
import torch
from torch.profiler import profile, ProfilerActivity
m = torch.hub.load('pytorch/vision:v0.10.0', 'resnet50', pretrained=True).cuda()
x = torch.randn(1,3,224,224).cuda()
with profile(activities=[ProfilerActivity.CUDA]) as p:
    m(x)
print(p.key_averages().table(sort_by='cuda_time_total', row_limit=15))
"
```

### Compare two profiles
```bash
python3 << 'EOF'
import csv
def load_metrics(f): return {r['Kernel']: float(r['SM%']) for r in csv.DictReader(open(f))}
m1, m2 = load_metrics('base.csv'), load_metrics('opt.csv')
for k in m1:
    print(f"{k}: {m1[k]:.1f}% → {m2[k]:.1f}% ({(m2[k]-m1[k]):+.1f}%)")
EOF
```

---

## Key Resources

- **Complete guide:** GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md
- **Quick start:** GPU_BOTTLENECK_QUICK_START.md
- **Workflows:** GPU_PROFILING_TOOLCHAIN.md
- **Tool comparison:** GPU_PROFILING_TOOL_COMPARISON.md
- **Full index:** GPU_PROFILING_INDEX.md

---

## Remember

1. **Profile first** - Never optimize blind
2. **Use roofline** - Clarifies compute vs memory
3. **Try multiple tools** - Cross-validate results
4. **Multiple runs** - Smooth out system noise (5-10 runs typical)
5. **Real data** - Use realistic workloads and batch sizes
6. **Iterate** - Profile → Optimize → Repeat until satisfied

