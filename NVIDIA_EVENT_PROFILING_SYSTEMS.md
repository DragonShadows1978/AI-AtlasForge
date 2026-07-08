# NVIDIA Event-Based GPU Profiling Systems: Comprehensive Research

## Executive Summary

NVIDIA event-based profiling systems provide detailed hardware-level insights into GPU performance through counter collection, memory access pattern analysis, and memory bandwidth metrics. This research synthesizes NVIDIA's official documentation, technical guides, and best practices for GPU profiling and analysis.

**Key Findings:**
- Event-based profiling captures low-level hardware metrics unavailable through standard CPU profilers
- Memory bandwidth is critical bottleneck identification - roofline models quantify compute-bound vs memory-bound kernels
- Multi-level memory hierarchy profiling (registers, L1, L2, global) reveals memory access efficiency
- Official NVIDIA tools (Nsight Systems, Nsight Compute) automate event collection and analysis
- Proper profiling workflow combines event metrics with source code correlation

---

## 1. NVIDIA Event-Based Profiling Fundamentals

### 1.1 What is Event-Based Profiling?

Event-based profiling in NVIDIA GPUs works by:

1. **Counter Collection**: Hardware performance counters track events during kernel execution
2. **Event Classification**: Events categorized as memory, compute, synchronization, or cache
3. **Metrics Calculation**: Raw counters combined to derive higher-level metrics
4. **Timeline Analysis**: Events timestamped for per-thread and per-warp analysis

**Advantages over sampling-based profiling:**
- No statistical approximation - captures all events
- Hardware-accurate timing and event attribution
- Per-warp and per-instruction visibility
- Direct correlation to GPU architecture

### 1.2 Historical Evolution

**NVIDIA GK110 (Kepler era):**
- 8 hardware performance counters per SM
- Limited event set (basic cache, memory, compute)

**Maxwell & Pascal Generation:**
- 16 counters per SM
- Expanded event domain coverage
- Introduction of kernel-level event filtering

**Volta & Turing Generation:**
- 32 hardware counters per SM
- Tensor core specific events
- Hierarchical memory profiling

**Ampere & Hopper (Current):**
- 64+ hardware counters per SM
- Advanced memory hierarchy events
- Instruction-level event attribution
- Unified memory access tracking

---

## 2. Memory Access Pattern Profiling

### 2.1 Memory Access Metrics

**Key Counter Categories:**

| Counter Type | Measures | Example Events |
|---|---|---|
| Global Memory | Data transfers to/from GDDR/HBM | `gld_request`, `gst_request` |
| L2 Cache | L2 hit/miss rates, bandwidth | `l2_subp_read_sector_queries`, `l2_subp_write_sector_queries` |
| L1 Cache | Per-thread cache behavior | `l1_cache_global_hit`, `l1_cache_global_miss` |
| Registers | Register file access patterns | `inst_executed_reg_reads`, `inst_executed_reg_writes` |
| Shared Memory | Inter-thread synchronization memory | `shared_load`, `shared_store` |

### 2.2 Memory Access Pattern Classification

**Coalesced Access Pattern:**
```
Ideal: Consecutive threads access consecutive memory addresses
Event: Single gld_request serves 32-thread warp
Metric: gld_efficiency = (optimal_instructions / actual_instructions) * 100%
```

**Uncoalesced Access Pattern:**
```
Non-ideal: Threads access scattered addresses
Event: Multiple gld_requests for single warp
Result: Reduced memory bandwidth utilization
```

### 2.3 Memory Profiling Workflow

1. **Baseline Measurement**: Profile without optimization
   - Record: `gld_request`, `gst_request`, `l1_cache_global_hit_rate`
   - Baseline throughput measurement

2. **Identify Bottlenecks**:
   - High `gld_request` count → Poor coalescing
   - High L1 miss rate → Cache pressure
   - High L2 miss rate → Memory bandwidth saturation

3. **Optimization Verification**:
   - Re-profile after code changes
   - Compare counter deltas
   - Validate improvement in application-level metrics

---

## 3. Memory Bandwidth Metrics and Bottleneck Identification

### 3.1 Memory Bandwidth Calculation

**Theoretical Peak Memory Bandwidth:**

For NVIDIA H100 (HBM3):
```
Bandwidth = Memory Clock × Memory Bus Width × 2 (DDR)
          = 1.215 GHz × 384-bit × 2
          = 3.35 TB/s (theoretical peak)
```

For RTX 4090 (GDDR6X):
```
Bandwidth = Memory Clock × Memory Bus Width × 2 (DDR)
          = 2.505 GHz × 384-bit × 2
          = 2.43 TB/s (theoretical peak)
```

### 3.2 Actual vs Theoretical Bandwidth

**Measured Bandwidth Formula:**
```
Actual_Bandwidth = (Total_Bytes_Transferred / Execution_Time) GB/s
```

**Measurement via Events:**
```
Bytes = (gld_request + gst_request) × 128 bytes (typical cache line)
Execution_Time = kernel_duration_ns
Bandwidth = Bytes / Execution_Time
```

### 3.3 Bandwidth Utilization Metrics

**Memory Efficiency Percentage:**
```
Memory_Efficiency = (Measured_Bandwidth / Theoretical_Peak) × 100%

Classification:
- > 80%: Excellent memory-bound efficiency
- 50-80%: Good, room for optimization
- 20-50%: Poor coalescing or cache issues
- < 20%: Severe memory access problems
```

### 3.4 Bottleneck Identification Framework

**Memory Bandwidth Bottleneck:**
- Symptom: `Measured_Bandwidth / Peak_Bandwidth` > 0.8 AND kernel compute time increases linearly with data size
- Profiler Event: High `dram_read_throughput`, `dram_write_throughput`
- Root Cause: Data movement dominates execution time
- Solution: Reduce data transfers, improve data reuse

**Memory Latency Bottleneck:**
- Symptom: Low thread occupancy, high `stall_memory_dependency` events
- Profiler Event: High L2/DRAM miss rates with few outstanding requests
- Root Cause: Insufficient latency hiding through warp scheduling
- Solution: Increase thread blocks, improve memory coalescing

**Cache Thrashing:**
- Symptom: High L1/L2 miss rate despite low bandwidth utilization
- Profiler Event: `l1_cache_global_miss`, `l2_subp_read_requests` >> actual data needs
- Root Cause: Working set exceeds cache size
- Solution: Improve data locality, use shared memory

---

## 4. Computation vs Memory-Bound Analysis (Roofline Model)

### 4.1 Roofline Model Fundamentals

The roofline model provides visual analysis of kernel performance bottlenecks:

```
Performance (GFLOPS)
    ↑
    │     ┌─── Compute Ceiling
    │    /│
    │   / │ Compute-Bound Region
    │  /  │
    │ /   │
    └─────┴─────────────── Memory-Bound Region
        Arithmetic Intensity (FLOP/Byte)
```

**Key Relationship:**
```
Max_Performance = min(
    Compute_Throughput,
    Memory_Bandwidth × Arithmetic_Intensity
)
```

### 4.2 Arithmetic Intensity Calculation

**Definition:**
```
AI = Floating_Point_Operations / Bytes_Transferred
```

**Example - Matrix Multiply (C = A × B, all N×N):**
```
FLOPs = 2 × N³
Bytes = 3 × N² (inputs: A, B; output: C, assuming in-cache reuse)
AI = 2N³ / 3N² = 2N/3

For N=1024: AI ≈ 682 FLOP/Byte (highly compute-bound)
For N=64:   AI ≈ 43 FLOP/Byte (moderately compute-bound)
For N=16:   AI ≈ 11 FLOP/Byte (memory-bound)
```

### 4.3 Identifying Bottleneck Classification

**Memory-Bound Kernels (AI < 5):**
```
Indicator Metrics:
- L1 hit rate < 50%
- L2 hit rate < 60%
- Achieved_Bandwidth / Peak_Bandwidth > 0.7
- Duration scales linearly with input size

Optimization Strategy:
- Reduce memory transfers
- Improve data locality
- Use shared memory effectively
```

**Compute-Bound Kernels (AI > 20):**
```
Indicator Metrics:
- SM utilization < 90%
- Few memory stalls
- Instruction throughput < peak (warp stalls)
- sm__throughput shows compute bottleneck

Optimization Strategy:
- Improve instruction-level parallelism
- Reduce register pressure
- Optimize warp scheduling
```

**Balanced Kernels (5 < AI < 20):**
```
Both compute and memory contribute
Optimization requires profiling both domains
Priority: whichever is most inefficient
```

### 4.4 Profiling Workflow for Roofline Analysis

**Step 1: Measure Compute Ceiling**
```
Kernel with maximum compute intensity
Counter: sm__inst_executed (all instructions)
Duration: kernel_time_ns
GFLOPS = (FMA_Instructions × 2 + FP_Instructions) / (kernel_time / 1e9)

Typical values:
- A100: ~312 TFLOPS (FP32), 624 TFLOPS (TensorOps)
- H100: ~756 TFLOPS (FP32), 1512 TFLOPS (TensorOps)
```

**Step 2: Measure Memory Ceiling**
```
Kernel with maximum memory bandwidth
Counter: dram_read_throughput, dram_write_throughput
Calculated: Peak_Memory_Bandwidth (from specs)
Example: H100 = 3.35 TB/s HBM3

Multiply by Arithmetic Intensity for crossover point
```

**Step 3: Plot Kernel Performance**
```
For each kernel under test:
- Calculate AI = FLOPs / Bytes
- Measure achieved GFLOPS (Nsight Compute)
- Plot on roofline
- Identify if above or below bandwidth line
```

---

## 5. Counter and Metric Collection

### 5.1 NVIDIA Hardware Performance Counters

**Primary Counter Categories:**

**1. Memory Counters:**
```
gld_request          # Global load requests (SM → L1)
gst_request          # Global store requests (SM → L1)
l1_cache_global_hit  # L1 cache hits for global memory
l1_cache_global_miss # L1 cache misses for global memory
l2_subp_read_sector_queries  # L2 read requests
l2_subp_write_sector_queries # L2 write requests
dram_read_throughput         # DRAM read bandwidth (bytes/s)
dram_write_throughput        # DRAM write bandwidth (bytes/s)
local_load           # Local (spilled register) load
local_store          # Local (spilled register) store
```

**2. Compute Counters:**
```
inst_executed           # Total instructions executed
inst_executed_fma       # Fused multiply-add instructions
inst_executed_fp32      # FP32 floating-point instructions
inst_executed_fp64      # FP64 floating-point instructions
inst_executed_int32     # INT32 integer instructions
sm__throughput          # SM instruction throughput
sm__warp_issue_stalled  # Warp stalls (various reasons)
```

**3. Cache Counters:**
```
l1_cache_global_hit_rate      # Hit rate percentage
l2_subp_read_hit_rate         # L2 hit rate
shared_load                   # Shared memory loads
shared_store                  # Shared memory stores
shared_load_conflict          # Shared memory bank conflicts
```

**4. Synchronization Counters:**
```
stall_memory_dependency  # Stalls waiting for memory
stall_sync               # Synchronization barriers
stall_exec_dependency    # Data dependencies
warp_divergence          # Branch divergence
```

### 5.2 NVIDIA Tools for Counter Collection

**Nsight Systems (System-Level Profiler):**
```
Use for: Whole-application profiling, CPU-GPU synchronization
Command: nsys profile --gpu-metrics-device=0 ./executable

Metrics:
- GPU kernel timeline
- Memory bandwidth utilization
- CPU-GPU transfer rates
- Context switches
- Page faults
```

**Nsight Compute (Kernel-Level Profiler):**
```
Use for: Deep kernel analysis
Command: ncu --set full ./executable

Advantages:
- Per-thread metrics
- Memory hierarchy breakdown (L1/L2/DRAM)
- Instruction-level analysis
- Roofline model data

Sections:
- Memory Workload Analysis
- Memory Hierarchy
- SM (Streaming Multiprocessor) Analysis
- Instruction MIX
```

**NVIDIA Profiler API:**
```cpp
#include <cuda_profiler_api.h>

cudaProfilerStart();  // Begin profiling
kernel<<<blocks, threads>>>();
cudaProfilerStop();   // End profiling
```

**CUPTI (CUDA Profiling Tools Interface):**
```cpp
#include <cupti.h>

// Subscribe to metrics
CUpti_MetricID metricId;
cuptiMetricGetIdFromName(device, "inst_executed", &metricId);

// Collect via callback on kernel event
CUpti_CallbackId callback_id;
cuptiSubscribe(&subscriber, callback, NULL);
```

### 5.3 Counter Collection Limitations

**Hardware Constraints:**
```
- Limited concurrent counters per SM (typically 4-8 slots)
- Multiplexing required for comprehensive analysis
- Multiplexing adds overhead (2-5% per multiplex pass)
- Some counters require kernel re-runs
```

**Workarounds:**
```
1. Profile in phases: Collect memory counters first, then compute
2. Use Nsight Compute's metric sets: Pre-configured counter groups
3. Enable sampling: Collect on subset of instructions (lower overhead)
4. Use derived metrics: Computed from available counters
```

---

## 6. Memory Hierarchy Profiling (L1, L2, Register)

### 6.1 GPU Memory Hierarchy Overview

**Typical NVIDIA GPU Memory Stack (Ampere/Hopper):**

```
┌─────────────────────────────────────┐
│     Registers (256 KB/thread)       │ ← Fastest, per-thread
├─────────────────────────────────────┤
│     Shared Memory (96-192 KB/SM)    │ ← Fast, SM-wide
├─────────────────────────────────────┤
│     L1 Cache (128 KB/SM, per-SM)    │ ← Per-SM coherent
├─────────────────────────────────────┤
│     L2 Cache (40 MB shared)         │ ← GPU-wide
├─────────────────────────────────────┤
│  Global Memory (HBM/GDDR, 80GB+)    │ ← Slowest, largest
└─────────────────────────────────────┘

Access Latencies (approximate):
- Registers: 0 cycles (immediate)
- Shared Memory: 20-30 cycles
- L1 Cache: 32-40 cycles
- L2 Cache: 200-300 cycles
- Global Memory: 200-400+ cycles
```

### 6.2 Register Pressure and Profiling

**Register File Architecture:**
```
- 256 KB per SM (Ampere)
- 32-bit register width
- 32 registers per thread typical
- Maximum threads per SM depends on register pressure

Formula: Max_Threads = (256 KB / Registers_Per_Thread) / 32 (threads/warp)

Example:
- 32 regs/thread: 256 = (256KB / 32 bytes) / 32
- 64 regs/thread: 128 threads maximum
- 96 regs/thread: 85 threads maximum (reduced occupancy)
```

**Register Profiling Metrics:**

| Counter | Meaning | Implication |
|---|---|---|
| `inst_executed_reg_reads` | Register file read operations | High = compute-bound |
| `inst_executed_reg_writes` | Register file write operations | High = memory→register flow |
| `sm__inst_issued` vs actual | Instruction issue rate | Low rate = register bottleneck |
| Register spills | Overflow to local memory | Critical overhead (100× slower) |

**Detecting Register Pressure:**
```
Symptoms:
1. Occupancy < 100% with low register count reported
2. High `local_load`, `local_store` counters
3. Unexplained performance loss with small code changes

Profiling:
nvcc -Xptxas -v -O3 kernel.cu
  → Shows register count per thread
  
Analysis:
- If register count > (256KB/32 threads) / 32 bytes: Spilling occurs
- Reduce register pressure: Loop unrolling reduction, variable scope
```

### 6.3 L1 Cache Profiling

**L1 Cache Architecture (per-SM):**
```
- 128 KB capacity per SM
- 128-byte cache line
- Per-thread granularity tracking
- Read/Write separate ports (can service multiple requests)

Events:
l1_cache_global_hit      # Successful L1 access
l1_cache_global_miss     # L1 miss → L2/DRAM fetch
l1_cache_global_hit_rate # Percentage calculation

Hit Rate Formula:
Hit_Rate = l1_cache_global_hit / (l1_cache_global_hit + l1_cache_global_miss)
```

**L1 Cache Behavior by Access Pattern:**

| Access Pattern | Hit Rate | Optimization |
|---|---|---|
| Sequential (coalesced) | 90-100% | Ideal, no action needed |
| Strided (constant stride) | 50-90% | May benefit from padding |
| Random | 5-20% | Use shared memory tiling |
| Broadcast (same address) | 100% | Cache-friendly |

**L1 Cache Optimization Example:**

```cuda
// Sub-optimal: Random access pattern
for (int i = 0; i < N; i++) {
    int idx = random_indices[i];
    result += data[idx];  // L1 miss likely
}

// Optimized: Use shared memory + prefetch
__shared__ int shared_data[BLOCK_SIZE];
for (int tile = 0; tile < TILES; tile++) {
    // Load into shared memory with coalescing
    shared_data[threadIdx.x] = data[tile * BLOCK_SIZE + threadIdx.x];
    __syncthreads();
    // Access from shared memory (cache-friendly)
    result += shared_data[threadIdx.x];
}
```

### 6.4 L2 Cache Profiling

**L2 Cache Architecture:**
```
- Shared across entire GPU (40 MB on A100, 96 MB on H100)
- 128-byte cache line (matches L1)
- 16 sub-partitions (Ampere/Hopper)
- All memory accesses go through L2

Events:
l2_subp_read_sector_queries      # L2 read accesses
l2_subp_write_sector_queries     # L2 write accesses
l2_subp_read_hit_rate            # Percentage
l2_subp_write_hit_rate           # Percentage
l2_subp_read_sysmem_replay       # System memory replays (coherency)
```

**L2 Cache Hit Rate Analysis:**

```
Typical Range:
- 70-80%: Well-optimized memory access
- 50-70%: Acceptable, some optimization potential
- 30-50%: Poor locality, significant optimization opportunity
- < 30%: Severe memory access issues

Investigation:
If L2 miss rate is high:
1. Check L1 hit rate: If L1 also low → Access pattern issues
2. Check data reuse: Loop over same data?
3. Check working set size: Exceeds 40MB?
4. Check stride pattern: Regular stride vs random?
```

**Working Set Analysis:**

```
If working_set_size >> L2_capacity:
  → Prefetch strategy recommended
  → Kernel decomposition or tiling
  
Example: Matrix multiplication
- Tile size selection affects L2 utilization
- If tile_size² < available_L2_space: Higher hit rate

Calculation:
L2_available_per_kernel = 40 MB / (concurrent_kernels)
Tile_Size = sqrt(L2_available_per_kernel / 3 / sizeof(float))
```

### 6.5 Memory Hierarchy Profiling Workflow

**Comprehensive Memory Profile:**

```
Step 1: L1 Analysis
  - Collect: l1_cache_global_hit_rate
  - Threshold: > 80% desired
  - If low: Check access pattern coalescing

Step 2: L2 Analysis
  - Collect: l2_subp_read_hit_rate
  - Threshold: > 70% desired
  - If low: Check working set vs cache size

Step 3: DRAM Analysis
  - Collect: dram_read_throughput, dram_write_throughput
  - Compare to peak bandwidth
  - If high: Memory-bound kernel
  - If low + high L2 miss: Cache issue

Step 4: Register Analysis
  - Collect: inst_executed_reg_reads, inst_executed_reg_writes
  - Check register spills (local_load/store)
  - If high: Consider register reduction

Step 5: Summary
  - Create bottleneck prioritization:
    1. Register spills (if present)
    2. L1 cache misses
    3. L2 cache misses
    4. DRAM saturation
```

---

## 7. Official NVIDIA Guides and Resources

### 7.1 NVIDIA Documentation

**Primary References:**

1. **CUDA Toolkit Documentation**
   - Path: `$CUDA_HOME/doc/`
   - Key Sections: Performance Analysis, Profiling
   - URL: https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/

2. **Nsight Systems User Guide**
   - Comprehensive profiling workflows
   - Event metric reference
   - Memory profiling case studies
   - URL: https://docs.nvidia.com/nsight-systems/

3. **Nsight Compute User Guide**
   - Kernel-level metrics definitions
   - Memory hierarchy analysis
   - Roofline model generation
   - URL: https://docs.nvidia.com/nsight-compute/

4. **NVIDIA GPU Architecture Documentation**
   - Per-generation counter specifications
   - Memory subsystem details
   - Event timing and attribution
   - URL: https://docs.nvidia.com/cuda/cuda-c-programming-guide/

5. **CUPTI API Reference**
   - Low-level profiling interface
   - Counter subscription API
   - Callback mechanism
   - URL: https://docs.nvidia.com/cupti/

### 7.2 Key NVIDIA Performance Metrics

**Standard Metric Set (Nsight Compute):**

| Metric Category | Key Metrics | Use Case |
|---|---|---|
| Memory | sm__average_dram_throughput_pct, l1_cache_global_hit_rate | Identify bandwidth bottleneck |
| Compute | sm__throughput, inst_executed | Compute saturation |
| L2 | l2_subp_read_hit_rate | Cache efficiency |
| Occupancy | sm__ctas_active, sm__warps_active | Thread parallelism |
| Stalls | stall_memory_dependency, stall_exec_dependency | Bottleneck timing |

### 7.3 Best Practices from NVIDIA

**1. Profiling Methodology:**
```
- Profile without optimizations first (baseline)
- Measure specific metrics, not everything
- Use multiplexing for comprehensive analysis
- Run multiple iterations (variance analysis)
- Validate with synthetic benchmarks
```

**2. Interpretation Guidelines:**
```
- Memory bandwidth > 70% peak: Memory-bound
- SM throughput < 80% peak: Compute bottleneck
- High stall counts: Identify stall reason (memory, exec, sync)
- Occupancy < 50%: Likely register pressure
```

**3. Tool Selection:**
```
- Nsight Systems: Application-level, CPU-GPU interaction
- Nsight Compute: Kernel-level deep analysis
- CUPTI: Programmatic custom profiling
- Nsys: Command-line production profiling
```

---

## 8. Practical Implementation Patterns

### 8.1 Basic Profiling Script (Nsight Compute)

```bash
#!/bin/bash
# Comprehensive GPU profiling workflow

CUDA_APP="./my_cuda_app"
OUTPUT_DIR="profiling_results"

mkdir -p $OUTPUT_DIR

# Profile 1: Memory-focused metrics
echo "Memory profiling..."
ncu --set memory_workload_analysis \
    -o $OUTPUT_DIR/memory_profile \
    $CUDA_APP

# Profile 2: Full system analysis
echo "Full system profiling..."
ncu --set full \
    -o $OUTPUT_DIR/full_profile \
    $CUDA_APP

# Profile 3: Memory hierarchy
echo "Memory hierarchy profiling..."
ncu --set memory_hierarchy \
    -o $OUTPUT_DIR/hierarchy_profile \
    $CUDA_APP

# Roofline model generation
ncu --set roofline \
    -o $OUTPUT_DIR/roofline \
    $CUDA_APP

echo "Profiling complete. Results in $OUTPUT_DIR/"
```

### 8.2 CUPTI-Based Counter Collection

```cpp
#include <stdio.h>
#include <cuda_runtime.h>
#include <cupti.h>

// Error checking macro
#define CUPTI_CALL(call)                                                \
  do {                                                                  \
    CUptiResult _status = (call);                                       \
    if (_status != CUPTI_SUCCESS) {                                     \
      const char *errstr;                                               \
      cuptiGetResultString(_status, &errstr);                           \
      printf("CUPTI error: %s\n", errstr);                              \
      exit(EXIT_FAILURE);                                               \
    }                                                                   \
  } while (0)

void profileKernel(void (*kernel)(), dim3 blocks, dim3 threads) {
    // Initialize CUPTI
    CUpti_SubscriberHandle subscriber;
    CUPTI_CALL(cuptiSubscribe(&subscriber, NULL, NULL));
    
    // Get metric IDs
    CUpti_MetricID metrics[4];
    const char *metric_names[] = {
        "inst_executed",
        "dram_read_throughput",
        "l1_cache_global_hit_rate",
        "l2_subp_read_hit_rate"
    };
    
    for (int i = 0; i < 4; i++) {
        CUPTI_CALL(cuptiMetricGetIdFromName(
            0, metric_names[i], &metrics[i]
        ));
    }
    
    // Enable metrics
    CUPTI_CALL(cuptiMetricEnable(metrics, 4));
    
    // Run kernel
    kernel<<<blocks, threads>>>();
    cudaDeviceSynchronize();
    
    // Read results
    uint64_t values[4];
    size_t value_sizes[4];
    for (int i = 0; i < 4; i++) {
        value_sizes[i] = sizeof(uint64_t);
    }
    CUPTI_CALL(cuptiMetricRead(metrics, 4, values, value_sizes));
    
    // Display results
    for (int i = 0; i < 4; i++) {
        printf("%s: %lu\n", metric_names[i], values[i]);
    }
    
    // Cleanup
    CUPTI_CALL(cuptiMetricDisable(metrics, 4));
    CUPTI_CALL(cuptiUnsubscribe(subscriber));
}
```

### 8.3 Roofline Model Analysis Script

```python
#!/usr/bin/env python3
import subprocess
import re
import matplotlib.pyplot as plt
import numpy as np

def extract_metrics(ncu_output):
    """Extract compute throughput and memory bandwidth from ncu output"""
    metrics = {}
    
    patterns = {
        'gflops': r'sm__throughput\s+(\d+\.?\d*)',
        'mem_bw': r'dram_throughput\s+(\d+\.?\d*)',
        'l1_hit': r'l1_cache_global_hit_rate\s+(\d+\.?\d*)',
        'l2_hit': r'l2_subp_read_hit_rate\s+(\d+\.?\d*)',
    }
    
    for name, pattern in patterns.items():
        match = re.search(pattern, ncu_output)
        if match:
            metrics[name] = float(match.group(1))
    
    return metrics

def plot_roofline(kernels_data, peak_compute=312, peak_memory=3.35):
    """Plot roofline model with kernel performance points"""
    
    # X-axis: Arithmetic Intensity (FLOP/Byte)
    ai = np.logspace(-1, 3, 1000)
    
    # Roofline: min(compute ceiling, memory_bandwidth * AI)
    compute_ceiling = peak_compute * np.ones_like(ai)
    memory_bound = peak_memory * ai
    roofline = np.minimum(compute_ceiling, memory_bound)
    
    # Crossover point
    crossover = peak_compute / peak_memory
    
    fig, ax = plt.subplots(figsize=(12, 8))
    
    # Plot roofline
    ax.loglog(ai, roofline, 'k-', linewidth=2, label='Roofline')
    ax.loglog(ai, compute_ceiling, 'b--', alpha=0.5, label='Compute Ceiling')
    ax.loglog(ai, memory_bound, 'r--', alpha=0.5, label='Memory Bound')
    
    # Plot kernel performance points
    colors = plt.cm.viridis(np.linspace(0, 1, len(kernels_data)))
    for i, (name, ai_val, gflops) in enumerate(kernels_data):
        ax.loglog(ai_val, gflops, 'o', color=colors[i], 
                 markersize=10, label=f'{name} (AI={ai_val:.1f})')
    
    ax.set_xlabel('Arithmetic Intensity (FLOP/Byte)', fontsize=12)
    ax.set_ylabel('Performance (GFLOPS)', fontsize=12)
    ax.set_title('GPU Roofline Model', fontsize=14)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    ax.set_ylim([1, peak_compute * 10])
    ax.set_xlim([0.1, 1000])
    
    plt.tight_layout()
    plt.savefig('roofline_analysis.png', dpi=300)
    plt.show()

if __name__ == '__main__':
    # Example: Run kernels and collect metrics
    # Replace with actual kernel execution
    
    kernels = [
        ('vector_add', 0.25, 50),      # Low AI, low performance
        ('matrix_mult', 3.5, 180),     # Medium AI, medium performance
        ('tensor_op', 50.0, 300),      # High AI, compute-bound
    ]
    
    plot_roofline(kernels)
```

### 8.4 Memory Hierarchy Analysis Template

```cuda
#include <stdio.h>
#include <cuda_runtime.h>

__global__ void memory_hierarchy_analysis(float *data, int N) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    
    if (idx < N) {
        // Register pressure measurement
        float reg_val1 = data[idx];
        float reg_val2 = reg_val1 * 2.5f;  // Increases register count
        float reg_val3 = reg_val2 + 1.2f;
        
        // L1 cache: Sequential access (coalesced)
        float l1_access = data[idx];
        
        // L2 cache: Strided access
        if (idx % 32 == 0) {
            float l2_access = data[idx * 32];
        }
        
        // Shared memory: Bank conflict potential
        __shared__ float shared_data[256];
        shared_data[threadIdx.x] = l1_access;
        __syncthreads();
        
        // Access with stride to avoid bank conflicts
        int shared_idx = (threadIdx.x + 1) % 32;
        float shared_val = shared_data[shared_idx];
        
        data[idx] = reg_val1 + shared_val;
    }
}

int main() {
    const int N = 1024 * 1024;
    float *d_data;
    cudaMalloc(&d_data, N * sizeof(float));
    
    // Profile: Use nsys or ncu to analyze
    // nsys profile ./program
    // ncu --set memory_hierarchy ./program
    
    memory_hierarchy_analysis<<<1024, 256>>>(d_data, N);
    cudaDeviceSynchronize();
    
    cudaFree(d_data);
    return 0;
}
```

---

## 9. Advanced Topics

### 9.1 Hardware-Specific Counter Availability

**Counter Support by Architecture:**

| Counter | Ampere | Hopper | Ada |
|---|---|---|---|
| dram_read_throughput | ✓ | ✓ | ✓ |
| l1_cache_global_hit_rate | ✓ | ✓ | ✓ |
| l2_subp_read_hit_rate | ✓ | ✓ | ✓ |
| tensor_precisions_used | ✓ | ✓ | ✓ |
| sm__warps_active | ✓ | ✓ | ✓ |

### 9.2 Unified Memory Profiling

**Unified Memory Access Tracking:**
```
Metrics:
- um_page_faults           # Unified memory page faults
- um_page_fault_latency    # Migration latency
- um_gpu_gups_inliners     # GPU->CPU data movement

Use: Track data migration overhead in unified memory codes
```

### 9.3 Tensor Core Specific Profiling

**Tensor Core Events (Ampere+):**
```
tensor_precisions_used_fp32   # TF32 operations
tensor_precisions_used_fp16   # FP16 operations
tensor_precisions_used_tf32   # TF32 (faster than FP32)

Analysis: Compare tensor core utilization across precisions
```

---

## 10. Summary and Recommendations

### Key Takeaways

1. **Event-based profiling** provides hardware-accurate, low-level GPU performance analysis
2. **Memory bandwidth** is the critical bottleneck in most GPU applications
3. **Roofline model** visually identifies compute-bound vs memory-bound kernels
4. **Memory hierarchy** (L1, L2, registers) analysis reveals optimization opportunities
5. **NVIDIA official tools** (Nsight Systems/Compute) automate metric collection
6. **Systematic profiling workflow** → Hypothesis → Test → Validate is essential

### Recommended Profiling Sequence

```
1. Baseline Profile (nsys)
   - Identify slowest kernels
   - Check CPU-GPU synchronization

2. Kernel Deep Analysis (ncu)
   - Measure all memory metrics
   - Calculate arithmetic intensity
   - Position on roofline model

3. Bottleneck-Specific Investigation
   - If memory-bound: Optimize coalescing, L1/L2 reuse
   - If compute-bound: Improve ILP, reduce register pressure
   - If latency-bound: Increase occupancy, improve warp scheduling

4. Optimization Verification
   - Re-profile after changes
   - Compare metrics to baseline
   - Validate application-level improvement
```

### Tool Selection Guide

| Scenario | Tool | Reason |
|---|---|---|
| Application performance overview | Nsight Systems | Timeline, CPU-GPU sync |
| Single kernel optimization | Nsight Compute | Detailed metrics, roofline |
| Custom metric collection | CUPTI API | Full programmatic control |
| Production profiling | nsys CLI | Minimal overhead, scriptable |
| Memory hierarchy deep-dive | Nsight Compute + Memory Hierarchy Set | L1/L2/DRAM detailed analysis |

---

## References

This research synthesizes information from:
- NVIDIA CUDA Toolkit Documentation (Official)
- NVIDIA Nsight Systems/Compute User Guides (Official)
- NVIDIA GPU Architecture Whitepapers (Official)
- CUDA Best Practices Guide (Official)
- Published NVIDIA technical blogs and webinars
- Academic GPU profiling research

**Last Updated:** 2026-07-07
**Research Scope:** NVIDIA event-based profiling, GPU architecture-specific (Ampere through Hopper generations)
