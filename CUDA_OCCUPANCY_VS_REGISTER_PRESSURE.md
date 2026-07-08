# CUDA Occupancy vs Register Pressure Tradeoffs: Comprehensive Guide

## Executive Summary

**Occupancy vs. Register Pressure** represents one of the most fundamental tradeoffs in CUDA kernel optimization. While conventional wisdom emphasizes maximizing occupancy (the ratio of active warps to maximum warps), modern high-performance kernels (FlashAttention, cuBLAS, TensorRT) deliberately reduce occupancy to preserve registers for computation, achieving superior performance through better instruction-level parallelism (ILP) and reduced memory pressure.

**Key Insight**: Occupancy is a means to an end (latency hiding), not an end in itself. High-register kernels hide latency through ILP instead of warp switching, often outperforming high-occupancy, register-starved kernels.

---

## 1. Core Concepts

### 1.1 Occupancy Definition and Mechanics

**Occupancy** = (Active Warps per SM) / (Maximum Warps per SM)

For modern GPUs (Ampere, Ada):
- Maximum warps per SM: 32 (for compute capability 8.0+)
- Maximum threads per SM: 2048
- Each warp = 32 threads

**Resource Constraints** (Ampere A100, 4x more for Ada H100):
- Registers per SM: 65,536 (32-bit registers)
- Shared memory per SM: 96 KB (configurable 48/96 KB split)
- Threads per block: 1024 (maximum)
- Blocks per SM: varies by resource usage

**Occupancy Calculation Example (Ampere)**:
```
SM: 108 SMs × 2048 threads = 221,184 total threads
Per-thread register limit: occupancy-dependent

If kernel uses 64 registers/thread:
  Available per SM: 65,536 / 64 = 1,024 threads
  Active warps: 1,024 / 32 = 32 warps
  Occupancy: 32 / 32 = 100% (but only for 1,024 threads!)

If kernel uses 128 registers/thread:
  Available per SM: 65,536 / 128 = 512 threads
  Active warps: 512 / 32 = 16 warps
  Occupancy: 16 / 32 = 50%
```

### 1.2 Latency Hiding Mechanisms

**Two Fundamental Methods:**

1. **Warp Switching (High-Occupancy Kernels)**
   - While one warp stalls on memory (L-cycles latency), scheduler switches to another warp
   - Requires ~2L occupancy to fully hide latency
   - Works well for memory-bound kernels with low ILP

2. **Instruction-Level Parallelism (Low-Occupancy Kernels)**
   - Single warp executes multiple independent instructions
   - Interleaves computation across dependencies
   - Can achieve 10-20 independent instructions per cycle
   - Hides latency through pipelining instead of warp switching

**Modern GPUs (Ampere+)**: Tensor Cores can hide 300-400 cycle latencies through ILP in matrix operations

### 1.3 Register Pressure and Spilling

**Register Spilling** occurs when:
- Kernel declares/uses more registers than available in register file
- Compiler automatically moves excess to local memory (slow!)
- Local memory = GPU DRAM through L2 cache (~200-400 cycles latency)

**Consequences of Spilling** (10-50x performance loss):
```
Spilled registers = slow DRAM access instead of <5 cycle register access
One spilled load: ~200 cycles latency
One spilled store: ~200-400 cycles latency
Matrix multiply: multiply latency × matrix dimensions
```

**Detection in Code**:
```cuda
// Nsight Compute metrics:
// - Memory Workload Analysis → Local Memory Load/Store
// - Indicates bytes spilled to local memory
// - Non-zero = register pressure problem

// Compiler output:
// ptxas warnings about register spilling
// Log file shows spill info:
//   "warning : ... registers needed, ... registers available"
```

### 1.4 Mathematical Relationships

**Performance Model** (simplified):
```
Throughput = (Pipeline Width × Clock Rate) / Max(Memory Latency, Computation Latency)

High-Occupancy, Spilling Kernel:
  Pipeline Width = reduced (stalls waiting for spill results)
  Memory Latency = high (spills to DRAM)
  Result: Limited throughput

Low-Occupancy, Register-Rich Kernel:
  Pipeline Width = full (no spills, independent instructions)
  Memory Latency = hidden by ILP
  Result: Higher throughput despite lower occupancy
```

**Occupancy vs Performance Relationship** (Non-linear):
- 0-25% occupancy: Often insufficient for latency hiding (memory-bound)
- 25-50% occupancy: Can be optimal for compute-bound, ILP-rich kernels
- 50-100% occupancy: Necessary only if low-ILP or high memory bandwidth needs
- **Beyond 50%**: Diminishing returns; additional occupancy rarely improves performance

**Roofline Model Application**:
```
Performance Ceiling = min(
  Peak Arithmetic Throughput,     # Gflops/s (limited by registers/ILP)
  Peak Memory Bandwidth × Arithmetic Intensity
)

Low-register kernels: Can't achieve peak throughput due to ILP limitations
High-register kernels: Can approach peak if memory bandwidth sufficient
```

---

## 2. Practical Techniques

### 2.1 __launch_bounds__ Directive

**Syntax**:
```cuda
__global__
__launch_bounds__(maxThreadsPerBlock, minBlocksPerMultiprocessor)
void optimized_kernel(/* args */) {
  // kernel body
}
```

**Parameters**:
- `maxThreadsPerBlock`: Maximum threads/block compiler assumes (default: 1024)
- `minBlocksPerMultiprocessor`: Minimum blocks/SM compiler must allow (optional)

**Effect on Compilation**:
- Compiler optimizes register usage assuming limited threadblock size
- Can **reduce** register allocation (better spill avoidance)
- Can **increase** register usage (if minBlocks high, reserves less for other blocks)
- Provides compiler hints for code generation

**Practical Examples**:

```cuda
// Example 1: Compute-bound kernel with high register usage
__global__
__launch_bounds__(256, 4)  // 256 threads/block, 4 blocks/SM
void gemm_kernel_256(/* args */) {
  // 256 × 4 = 1024 threads/SM = 32 warps = 100% occupancy if registers allow
  // Compiler optimizes assuming this layout
}

// Example 2: Memory-bound kernel needing high occupancy
__global__
__launch_bounds__(512, 8)
void memory_kernel_512(/* args */) {
  // 512 × 8 = 4096 threads (impossible!)
  // This tells compiler: "Expect my block to use little memory/registers"
  // Compiler can then fit more blocks
}

// Example 3: Explicitly low-occupancy design
__global__
__launch_bounds__(128, 1)  // Only 1 block per SM, 128 threads
void low_occ_kernel(/* args */) {
  // Tells compiler: "I'm compute-dense, allocate 4,096 registers per thread if needed"
  // Actual occupancy = 128/32 = 4 warps = 12.5% (but with massive per-warp resources)
}
```

**Gotchas**:
- `minBlocksPerMultiprocessor` is not a guarantee—it's a hint
- If actual occupancy can't achieve the minimum without excessive spilling, it's ignored
- Over-specifying can force unnecessary spilling (wrong hint)
- Under-specifying wastes resources

### 2.2 -maxrregcount Compiler Flag

**Usage**:
```bash
nvcc -maxrregcount=64 kernel.cu -o kernel.cubin
```

**Effect**:
- Forces compiler to use **at most N registers** per thread
- Excess variables spilled to local memory automatically
- Applies globally to all kernels in compilation unit

**Tradeoff**:
- Lowers occupancy (fewer threads with N registers fit per SM)
- **But also prevents spilling** (predictable performance)
- Enables high-occupancy with reasonable register pressure

**Example: Progressive Register Limiting**:
```bash
# Baseline: No limit, may spill at 95 registers
nvcc kernel.cu -O3

# Conservative: Force 64 regs/thread, prevent spilling
nvcc -maxrregcount=64 kernel.cu -O3

# Aggressive: Force 48 regs/thread, maximize occupancy
nvcc -maxrregcount=48 kernel.cu -O3
```

**Typical Values by Architecture**:
- Ampere (A100): 48-96 registers/thread common for high-occupancy kernels
- Ada (H100): 32-64 registers/thread due to higher memory bandwidth
- Kepler: 64-128 registers/thread (larger register file)

### 2.3 Manual Register Capping Strategies

**Strategy 1: Pragma-Based Limiting** (Not standard, compiler-specific)
```cuda
#pragma omp declare target to(kernel)
__global__ void kernel() {
  // Limited-register version
  // Manually avoid using extra locals
}
#pragma omp end declare target
```

**Strategy 2: Explicit Variable Reduction**:
```cuda
// HIGH-REGISTER VERSION (SpillIf)
__global__ void matmul_high_reg() {
  float acc[8][8];  // 64 accumulator floats
  // ... compute with all 64 values in registers
}

// LOW-REGISTER VERSION (Manual spilling)
__global__ void matmul_low_reg() {
  float acc[4][4];  // Only 16 at a time in registers
  // Reuse same registers for different partial results
  // Trade: computation complexity for register pressure
}
```

**Strategy 3: Volatile Register Hints**:
```cuda
// Hint to compiler: This variable might be needed again, keep it around
volatile float temp = __expf(x);
result += temp * coeff;

// Without volatile, compiler might free the register between operations
```

**Strategy 4: Register-to-Shared Memory Promotion**:
```cuda
// Original: Uses many registers
__global__ void kernel_high_reg() {
  float tile[32][32];  // 1,024 floats × 32 threads = 32 KB registers (!)
  // ... do work
}

// Optimized: Use shared memory + fewer registers
__shared__ float tile[32][32];
__global__ void kernel_shared() {
  // Each thread: only indices + temp variables in registers
  // Shared memory holds tile data (fast, 96 KB available)
}
```

---

## 3. Real-World Examples

### 3.1 FlashAttention: Deliberate Low-Occupancy Design

**Paper**: "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness" (Dao et al., 2022)
**GitHub**: https://github.com/HazyResearch/flash-attention

**Design Philosophy**:
- Attention kernel is **compute-bound** (not memory-bound as commonly assumed)
- Benefits from high register usage for intermediate vectors
- Low occupancy (12.5% on Ampere) with ~1,000 registers per thread
- Optimizes for **throughput**, not occupancy

**Kernel Characteristics** (Ampere A100):
```
Threads per block: 128
Registers per thread: ~1,024 (32-bit)
Occupancy: 128 / 32 warps = 4 warps per SM = 12.5%
Active threads per SM: 128 (vs 2,048 possible)

Latency hiding: ILP, not warp switching
Key-value cache fits in registers, not shared memory
Result: 3-4x faster than standard attention
```

**Code Structure** (Simplified):
```cuda
__global__
__launch_bounds__(128, 1)  // Explicitly low-occupancy
void flash_attn_fwd_kernel(/* Q, K, V, O buffers */) {
  // Thread block = 128 threads
  // Each block processes one full attention head
  
  // Per-thread registers:
  float query_regs[64];      // Q values loaded once
  float kv_cache[128];       // K, V tile in registers
  float scores[16];          // Attention scores
  float context[64];         // Output context vector
  
  // Main loop: iterate over key-value tiles
  for (int tile_idx = 0; tile_idx < num_kv_tiles; ++tile_idx) {
    load_kv_tile_to_registers();
    compute_scores();
    update_context();
    __syncthreads();  // Minimal synchronization
  }
  
  normalize_and_store_context();
}
```

**Performance Impact**:
- Peak TFlops: 120 TFlops (A100) vs. 312 TFlops theoretical
- Bandwidth: Limited by register pressure, not memory bandwidth
- **Speedup vs. naive attention**: 3-4x faster despite lower occupancy
- **Memory efficiency**: 10x less data movement through HBM

**Key Lesson**: Register-rich, low-occupancy design beats high-occupancy, register-starved design when ILP is available.

---

### 3.2 cuBLAS and TensorRT GEMM Kernels

**cuBLAS Optimization Strategy** (Analyzed from NVIDIA source, open partially):
- Different kernels for different shapes/precision
- Large matrices (M, N, K > 512): High-register GEMM kernels
- Small matrices: Occupancy-focused kernels

**Typical cuBLAS GEMM (A100, fp32)** for M=N=K=4096:
```
Block dimensions: 128×32 = 4,096 threads/block (!)
Registers per thread: ~128
Occupancy: Impossible (4,096 > 2,048 threads/SM)
Actually uses: 1 block per SM, partial-block semantics

Wait, correction: More commonly:
Block dimensions: 256×1 = 256 threads
Registers: ~96
Occupancy: 256 / 32 warps = 8 warps = 25% per block
But SM can hold 8 blocks = 8 × 256 = 2,048 threads = 64 warps = 100% occupancy overall
```

**TensorRT Kernel Choices** (from documentation):
- Selects kernel based on:
  1. Tensor shapes and precision
  2. Target GPU architecture
  3. Occupancy requirements
  4. Register pressure limits

**Example: FP8 GEMM Kernel (High-Register)**:
```cuda
// TensorRT uses specialized kernels like:
__global__
__launch_bounds__(256, 4)
void gemm_tensorrt_fp8(/* inputs */) {
  // 256 threads × 4 blocks = 1,024 threads/SM
  // Each thread: ~96 registers (fp8 compressed, still expensive)
  // Occupancy: 100%
  
  // Technique: Sub-warp synchronization
  // 256 threads = 8 warps
  // Some warps stall for memory, others compute
  // ILP within each warp hides latency
}
```

**Key Insight**: Industrial libraries balance occupancy (for guaranteed latency hiding) with register pressure (for ILP).

---

### 3.3 Attention Mechanisms Beyond FlashAttention

**Multi-Query Attention (High-Occupancy)**:
```cuda
__global__
__launch_bounds__(512, 2)  // Higher occupancy than FlashAttention
void multi_query_attention(/* Q, K_compressed, V_compressed */) {
  // K, V have fewer heads than Q (shared across queries)
  // Lower register pressure than full attention
  // Occupancy: 512 / 32 = 16 warps = 50%
  // Register pressure: ~64 regs/thread
  
  // Good balance: Still enough registers for main computation
  // Higher occupancy captures some warp-switching benefits
}
```

**Sparse Attention (Memory-Bound)**:
```cuda
__global__
__launch_bounds__(1024, 2)  // High occupancy needed
void sparse_attention() {
  // Sparse patterns → unpredictable memory access
  // Can't use all registers for computation
  // Need high occupancy to hide memory latency
  
  // Occupancy: 1,024 / 32 = 32 warps = 100%
  // Register pressure: ~32 regs/thread (reduced for occupancy)
  // Works because memory latency dominates
}
```

---

### 3.4 Other Compute-Heavy Kernels

**Reduction Kernels**:
```cuda
// High-occupancy reduction (log N stages)
__global__
__launch_bounds__(1024, 1)
void reduce_sum_high_occ() {
  __shared__ float smem[1024];
  // Memory-bound on shared memory and global memory
  // High occupancy hides latency
  // Register usage: ~16 (mostly indices)
}

// Fast reduction with registers (sequential consistency)
__global__
__launch_bounds__(256, 1)
void reduce_sum_fast() {
  float local_sum = 0;
  // Sequential thread-local reduction
  // Each thread reduces own chunk in registers
  // Low occupancy OK (compute-bound, high ILP)
  // Register usage: ~48-64
}
```

**Convolution Kernels** (tiling-based):
```cuda
// Typical conv kernel: tiled computation
__global__
__launch_bounds__(128, 4)
void conv2d_tile_256() {
  __shared__ float input_tile[16][16];
  __shared__ float filter_weights[3][3];
  
  // Per-thread: ~96 registers
  // 128 threads × 4 blocks = 512 threads/SM
  // Occupancy: 512 / 32 = 16 warps = 50%
  // Tile computation in registers, boundary accesses to global
  // Good balance: High computation, moderate occupancy for synchronization
}
```

---

## 4. Profiling and Tuning Workflow

### 4.1 Nsight Compute Profiling for Register Analysis

**Installation**:
```bash
# NVIDIA Nsight Systems (CLI tool)
apt-get install nvidia-nsight-systems

# Nsight Compute (full GUI, recommended for register analysis)
# Download from: https://developer.nvidia.com/tools-overview/nsight-compute
```

**Basic Profiling Workflow**:

```bash
# 1. Profile baseline kernel
ncu --set roofline_chart,memory_workload_analysis -o baseline.ncu-rep ./your_kernel

# 2. Open in GUI:
# - ncu-ui baseline.ncu-rep
# - Or analyze with CLI
```

**Key Metrics for Register Analysis**:

1. **Register Usage**:
   - `sm__registers_per_thread`: Registers per thread (absolute)
   - Calculate: registers / threads per block

2. **Memory Spill Analysis**:
   - `dram__sectors_read` (via LOCAL_MEM): Read local memory
   - `dram__sectors_write` (via LOCAL_MEM): Write local memory
   - Non-zero = register pressure problem

3. **Occupancy Metrics**:
   - `sm__occupancy`: Actual occupancy achieved (%)
   - `sm__warps_active`: Average active warps per cycle
   - `sm__cycles_elapsed`: Total execution cycles

4. **Performance Metrics**:
   - `smsp__throughput`: Average instruction throughput (IPC)
   - `sm__pipe_fu_utilization`: Functional unit utilization
   - `sm__pipe_tensor_utilization`: Tensor core utilization

**Practical Analysis Workflow**:

```bash
# Profiling script
cat > profile_kernel.sh << 'EOF'
#!/bin/bash

KERNEL=$1
OUTPUT=$2

# Full counter collection
ncu \
  --set full \
  --csv \
  -o ${OUTPUT}.ncu-rep \
  ./${KERNEL}

# Export results
ncu --csv -o ${OUTPUT}.csv ${OUTPUT}.ncu-rep

# Key metrics to examine
echo "=== Register Analysis ==="
grep "sm__registers_per_thread" ${OUTPUT}.csv

echo "=== Spill Analysis ==="
grep "local_" ${OUTPUT}.csv | head -5

echo "=== Occupancy ==="
grep "sm__occupancy" ${OUTPUT}.csv

echo "=== Instruction Throughput ==="
grep "smsp__inst_executed_per_cycle_active" ${OUTPUT}.csv
EOF

chmod +x profile_kernel.sh
./profile_kernel.sh kernel_baseline baseline
./profile_kernel.sh kernel_optimized optimized
```

### 4.2 Register Limiting Workflow

**Step-by-Step Tuning**:

```bash
# 1. Measure baseline register usage
nvcc -O3 kernel.cu -o kernel_baseline.cubin
# Extract register info from cubin (ptxas output during compilation)

# 2. Try progressive limits
for REG_LIMIT in 128 96 64 48 32; do
  nvcc -O3 -maxrregcount=$REG_LIMIT kernel.cu -o kernel_reg${REG_LIMIT}.cubin
  ./benchmark_kernel kernel_reg${REG_LIMIT}.cubin \
    | tee benchmark_reg${REG_LIMIT}.txt
done

# 3. Create tuning curve (Occupancy vs Performance)
# Plot register limit vs. achieved throughput (TFlops)
# Identify knee point where occupancy no longer helps
```

**Tuning Data Example** (Hypothetical):
```
Register Limit | Regs/Thread | Occupancy | Throughput | Speedup
    Unlimited  |     128     |   25%     |  120 TF/s  |  1.0x
        128    |     128     |   25%     |  120 TF/s  |  1.0x (no spilling)
         96    |      96     |   50%     |  125 TF/s  |  1.04x (slightly better)
         64    |      64     |   75%     |  118 TF/s  |  0.98x (occupancy doesn't help)
         48    |      48     |  100%     |  110 TF/s  |  0.92x (spilling starts)

Optimal: 96 registers (occupancy 50%, no spilling, best throughput)
```

### 4.3 Instruction-Level Parallelism (ILP) Measurement

**ILP Metrics**:
- **Instructions Per Cycle (IPC)**: Higher = better ILP
  - Ampere: Target 4-8 IPC (peak ~8)
  - Ada: Target 4-8 IPC (peak ~8)

**Profiling ILP**:

```bash
ncu --set smsp_inst_executed_per_cycle_active -o ilp.ncu-rep ./kernel

# Analyze:
# - < 2 IPC: Likely memory-bound or data dependency
# - 2-4 IPC: Decent ILP, room for improvement
# - 4-6 IPC: Good ILP
# - > 6 IPC: Excellent (rare, requires careful scheduling)
```

**Increasing ILP**:

1. **Increase instruction independence**:
```cuda
// LOW ILP: Dependencies between operations
float result = a * b;
result = result + c;        // Wait for multiply
result = result * d;        // Wait for add
result = result / e;        // Wait for multiply
// 4 operations = ~4 cycles (1 IPC)

// HIGH ILP: Independent operations
float temp1 = a * b;
float temp2 = c * d;
float temp3 = e * f;        // All independent, execute in parallel
float result = temp1 + temp2 + temp3;
// 3 adds/4 multiplies = ~2 cycles (2+ IPC)
```

2. **Loop unrolling** (expose independent iterations):
```cuda
// LOW ILP: Sequential iterations
for (int i = 0; i < N; ++i) {
  result += data[i];        // Each iteration depends on previous
}

// HIGH ILP: Unrolled, independent
for (int i = 0; i < N; i += 4) {
  float acc0 = 0, acc1 = 0, acc2 = 0, acc3 = 0;
  acc0 += data[i];
  acc1 += data[i+1];
  acc2 += data[i+2];        // All independent
  acc3 += data[i+3];
  result += acc0 + acc1 + acc2 + acc3;
}
```

3. **Prefetching**:
```cuda
// Prefetch next tile while computing current tile
for (int tile = 0; tile < num_tiles; ++tile) {
  if (tile > 0) compute_tile(tile - 1);
  if (tile < num_tiles - 1) prefetch_tile(tile + 1);
}
```

### 4.4 Performance Prediction Models

**Simple Model** (Roofline-based):

```
Effective Peak Throughput = min(
  Peak_Flops × (Registers_per_thread / 128) × (ILP_factor / 4),
  Peak_Bandwidth × Arithmetic_Intensity
)

Register Penalty: Each 128 regs = potential 1.0x throughput (fully utilized)
ILP Factor: 4 = baseline; 2-8 realistic range
```

**Example**: A100, GEMM kernel
```
Peak Flops (TF32): 312 TF/s (Tensor Cores)
Register allocation: 96 per thread
ILP achieved: 6 (good, from profiling)
Arithmetic Intensity: 8 (ops per byte from DRAM)
Peak Bandwidth: 2 TB/s

Predicted Throughput = min(
  312 × (96/128) × (6/4),           # Register × ILP component
  2000 × 8 / 10^6                    # Memory bandwidth component (simplified)
)
= min(312 × 0.75 × 1.5, 16)
= min(351, 16)
= 16 TF/s  (memory-bound, so estimate is wrong; ignoring details)
```

**Better Approach**: Use Nsight Compute's Roofline Chart
- Automatically accounts for all factors
- Shows achieved throughput vs. peak
- Indicates bottleneck (compute vs. memory)

---

## 5. Code Examples and Patterns

### 5.1 Kernel with __launch_bounds__ Variations

**Example: Matrix Multiply with Different Launch Bounds**:

```cuda
// Version 1: Baseline (no hints)
__global__ void gemm_baseline(
    const float* A, const float* B, float* C, int N) {
  int row = blockIdx.y * blockDim.y + threadIdx.y;
  int col = blockIdx.x * blockDim.x + threadIdx.x;
  
  float acc = 0.0f;
  for (int k = 0; k < N; ++k) {
    acc += A[row * N + k] * B[k * N + col];
  }
  C[row * N + col] = acc;
}

// Version 2: High-occupancy (256×256 block, 100% occupancy)
__global__
__launch_bounds__(256, 4)
void gemm_high_occ(
    const float* A, const float* B, float* C, int N) {
  // grid: (N/16, N/16), block: (16, 16) = 256 threads
  // 256 × 4 = 1,024 threads/SM = 32 warps = 100% occupancy possible
  // Compiler: Assumes 256-thread block, limits register usage
  
  int row = blockIdx.y * blockDim.y + threadIdx.y;
  int col = blockIdx.x * blockDim.x + threadIdx.x;
  
  float acc = 0.0f;
  for (int k = 0; k < N; ++k) {
    acc += A[row * N + k] * B[k * N + col];
  }
  C[row * N + col] = acc;
}

// Version 3: Tile-based with shared memory (balanced)
__global__
__launch_bounds__(128, 2)
void gemm_tiled(
    const float* A, const float* B, float* C, int N) {
  // Block: 128 threads = 4 warps (12.5% occupancy if one block)
  // But supports 2 blocks/SM = 8 warps = 25% occupancy
  // Uses tile_size=32 → 32×32 = 1,024 floats = 4 KB per tile
  // Fits in shared memory, high computation/memory ratio
  
  const int tile_size = 32;
  __shared__ float A_tile[tile_size][tile_size];
  __shared__ float B_tile[tile_size][tile_size];
  
  int row = blockIdx.y * blockDim.x + threadIdx.x;
  int col = blockIdx.x * blockDim.x + threadIdx.x;
  
  float acc = 0.0f;
  
  for (int t = 0; t < N / tile_size; ++t) {
    // Load tile
    int a_idx = (row / tile_size) * tile_size + threadIdx.x;
    int b_idx = threadIdx.x * N + col % tile_size;
    if (threadIdx.x < tile_size) {
      A_tile[threadIdx.x / tile_size][threadIdx.x % tile_size] = 
        A[row * N + t * tile_size + threadIdx.x];
      B_tile[threadIdx.x / tile_size][threadIdx.x % tile_size] = 
        B[(t * tile_size + threadIdx.x) * N + col];
    }
    __syncthreads();
    
    // Compute
    for (int k = 0; k < tile_size; ++k) {
      acc += A_tile[threadIdx.x / tile_size][k] * 
             B_tile[k][threadIdx.x % tile_size];
    }
    __syncthreads();
  }
  
  C[row * N + col] = acc;
}

// Version 4: Low-occupancy, compute-dense (FlashAttention style)
__global__
__launch_bounds__(128, 1)
void gemm_low_occ_registers(
    const float* A, const float* B, float* C, int N) {
  // 128 threads × 1 block/SM = 4 warps = 12.5% occupancy
  // Each thread: many registers for local computation
  // NO shared memory: stays in registers
  
  // Each thread computes 8×8 = 64 output values
  const int subblock_size = 8;
  
  __shared__ float A_tile[128];  // Small, only for coordination
  __shared__ float B_tile[128];
  
  float C_local[subblock_size][subblock_size];
  #pragma unroll
  for (int i = 0; i < subblock_size; ++i) {
    #pragma unroll
    for (int j = 0; j < subblock_size; ++j) {
      C_local[i][j] = 0.0f;
    }
  }
  
  int start_row = blockIdx.y * blockDim.x * subblock_size + threadIdx.x * subblock_size;
  int start_col = blockIdx.x * blockDim.x * subblock_size + threadIdx.x * subblock_size;
  
  // Main computation loop (stays in registers)
  for (int k = 0; k < N; ++k) {
    #pragma unroll
    for (int i = 0; i < subblock_size; ++i) {
      #pragma unroll
      for (int j = 0; j < subblock_size; ++j) {
        float a_val = A[(start_row + i) * N + k];
        float b_val = B[k * N + (start_col + j)];
        C_local[i][j] += a_val * b_val;
      }
    }
  }
  
  // Store results
  #pragma unroll
  for (int i = 0; i < subblock_size; ++i) {
    #pragma unroll
    for (int j = 0; j < subblock_size; ++j) {
      C[(start_row + i) * N + (start_col + j)] = C_local[i][j];
    }
  }
}
```

**Launch Comparison**:

```cuda
void benchmark_all() {
  const int N = 4096;
  float *A, *B, *C;
  cudaMalloc(&A, N * N * sizeof(float));
  cudaMalloc(&B, N * N * sizeof(float));
  cudaMalloc(&C, N * N * sizeof(float));
  
  // Version 1: Baseline
  dim3 grid_base((N + 15) / 16, (N + 15) / 16);
  dim3 block_base(16, 16);
  gemm_baseline<<<grid_base, block_base>>>(A, B, C, N);
  // Expected: High occupancy, mediocre performance (naive computation)
  
  // Version 2: High-occupancy
  dim3 grid_occ((N + 15) / 16, (N + 15) / 16);
  dim3 block_occ(16, 16);  // Still 256 threads
  gemm_high_occ<<<grid_occ, block_occ>>>(A, B, C, N);
  // Expected: 100% occupancy, similar performance to baseline
  
  // Version 3: Tiled
  dim3 grid_tile((N + 31) / 32, (N + 31) / 32);
  dim3 block_tile(128);
  gemm_tiled<<<grid_tile, block_tile>>>(A, B, C, N);
  // Expected: Better compute efficiency through tiling, 25% occupancy
  
  // Version 4: Low-occupancy registers
  dim3 grid_low((N + 1024) / 1024, (N + 1024) / 1024);  // Each block: 128×8 outputs
  dim3 block_low(128);
  gemm_low_occ_registers<<<grid_low, block_low>>>(A, B, C, N);
  // Expected: 12.5% occupancy, best compute efficiency (if enough registers)
}
```

### 5.2 Register-Limited Kernel Pattern

```cuda
// Pattern: Explicit register reduction through algorithm modification

__global__
__launch_bounds__(512, 2)
void reduce_sum_registered(
    const float* input, float* output, int N) {
  // Fixed register budget: ~48 registers per thread
  // Strategy: Process input in sequential chunks
  
  const int thread_work = 8;  // Each thread processes 8 elements
  const int smem_size = 512;
  __shared__ float smem[smem_size];
  
  float local_sum = 0.0f;  // Single accumulator in register
  float temp = 0.0f;       // Temp register for load
  
  // Load phase: Sequential, memory-bound (OK for 512 threads/block)
  for (int i = threadIdx.x; i < N; i += blockDim.x * thread_work) {
    #pragma unroll 4
    for (int j = 0; j < thread_work; ++j) {
      temp = input[i + j * blockDim.x];
      local_sum += temp;  // All fits in 2 registers
    }
  }
  
  // Store in shared memory for reduction
  smem[threadIdx.x] = local_sum;
  __syncthreads();
  
  // Tree reduction (uses logarithmic shared memory accesses)
  for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
    if (threadIdx.x < stride) {
      smem[threadIdx.x] += smem[threadIdx.x + stride];  // Reuse 2 registers
    }
    __syncthreads();
  }
  
  if (threadIdx.x == 0) {
    output[blockIdx.x] = smem[0];
  }
}
```

### 5.3 Profiling and Tuning Script

```python
#!/usr/bin/env python3
"""
CUDA kernel tuning harness: Explore occupancy vs register tradeoffs
"""

import subprocess
import re
import tempfile
import os
from pathlib import Path

class KernelTuner:
    def __init__(self, kernel_name, source_file):
        self.kernel_name = kernel_name
        self.source_file = source_file
        self.results = []
    
    def compile_variant(self, max_registers):
        """Compile kernel with register limit"""
        output_cubin = f"/tmp/{self.kernel_name}_reg{max_registers}.cubin"
        cmd = [
            "nvcc",
            "-O3",
            f"-maxrregcount={max_registers}",
            "-gencode", "arch=compute_80,code=sm_80",  # Ampere
            self.source_file,
            "-o", output_cubin
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Compilation failed for {max_registers}: {result.stderr}")
            return None
        
        return output_cubin
    
    def profile_kernel(self, cubin_path):
        """Profile with Nsight Compute"""
        output_report = cubin_path.replace(".cubin", ".ncu-rep")
        cmd = [
            "ncu",
            "-o", output_report,
            "--set", "full",
            cubin_path
        ]
        
        subprocess.run(cmd, capture_output=True)
        
        # Parse report (simplified; real parsing more complex)
        metrics = {
            "registers": None,
            "occupancy": None,
            "ipc": None,
            "throughput": None
        }
        
        # (In practice, use ncu-ui or write comprehensive parser)
        return metrics
    
    def tune(self, reg_limits=[128, 96, 64, 48, 32]):
        """Test multiple register limits"""
        print(f"Tuning {self.kernel_name}...")
        print(f"Register limits: {reg_limits}")
        
        for max_reg in reg_limits:
            print(f"\n=== Testing max_registers={max_reg} ===")
            
            # Compile
            cubin = self.compile_variant(max_reg)
            if not cubin:
                continue
            
            # Profile
            metrics = self.profile_kernel(cubin)
            metrics['max_registers'] = max_reg
            self.results.append(metrics)
            
            print(f"Registers: {metrics.get('registers')}")
            print(f"Occupancy: {metrics.get('occupancy')}")
            print(f"IPC: {metrics.get('ipc')}")
    
    def report(self):
        """Print tuning results"""
        print("\n=== Tuning Summary ===")
        print("max_reg\tRegisters\tOccupancy\tIPC\tThroughput")
        for r in self.results:
            print(f"{r['max_registers']}\t{r['registers']}\t{r['occupancy']}\t{r['ipc']}\t{r['throughput']}")


# Example usage:
if __name__ == "__main__":
    tuner = KernelTuner("my_kernel", "kernel.cu")
    tuner.tune(reg_limits=[128, 96, 64, 48, 32])
    tuner.report()
```

---

## 6. Decision Framework

### When to Choose High Occupancy:
1. **Memory-bound kernels** (few operations per byte):
   - Sparse tensor operations
   - Simple reductions
   - Copy kernels
   - **Strategy**: Maximize occupancy to hide memory latency

2. **Synchronization-heavy kernels**:
   - Iterative algorithms with frequent barriers
   - **Strategy**: Ensure enough occupancy for asynchronous progress

3. **Unpredictable memory access patterns**:
   - Irregular sparse data structures
   - **Strategy**: High occupancy hides latency spikes

### When to Choose Low Occupancy:
1. **Compute-bound kernels** (high FLOPs per byte):
   - Matrix operations
   - Attention mechanisms
   - Specialized FFTs
   - **Strategy**: Use registers for computation, not warp switching

2. **Data reuse potential**:
   - Tiling opportunities
   - Registers can hold tile data
   - **Strategy**: Keep working set in registers

3. **Latency-sensitive operations**:
   - Want predictable, minimum latency
   - Low occupancy = no queueing
   - **Strategy**: Dedicate resources to single block

### Diagnostic Flowchart:

```
1. Profile kernel
   ├─ Occupancy < 25%?
   │  └─ Consider: Are registers limiting? Can you reduce?
   │
   ├─ Local memory (spills) > 0%?
   │  └─ Register pressure problem: Reduce occupancy goal OR optimize algorithm
   │
   ├─ IPC > 4?
   │  └─ Good: Either occupancy or ILP working
   │
   ├─ IPC < 2?
   │  └─ Bad: Check for memory latency → increase occupancy
   │                 OR Check for dependencies → reduce register usage, more ILP potential
   │
   └─ Performance plateau across occupancies?
      └─ Likely algorithmic limit: Revisit computation structure
```

---

## 7. Summary Table

| Factor | High-Occupancy Kernels | Low-Occupancy Kernels |
|--------|------------------------|----------------------|
| **Occupancy** | 75-100% | 12-50% |
| **Registers/thread** | 16-64 | 64-256+ |
| **Latency Hiding** | Warp switching | ILP + pipelining |
| **Best For** | Memory-bound ops | Compute-bound ops |
| **Examples** | Sparse ops, reductions | GEMM, attention, FFT |
| **Synchronization** | Frequent barriers | Minimal barriers |
| **Shared Memory Usage** | Often critical | Optional |
| **__launch_bounds__** | Hint high maxThreads | Hint low maxThreads |
| **Performance Metric** | Throughput (GiB/s) | Throughput (TFlops) |

---

## References and Further Reading

**Official NVIDIA Documentation**:
- CUDA C++ Programming Guide: https://docs.nvidia.com/cuda/cuda-c-programming-guide/
  - Chapter: Registers and Memory
  - Chapter: Occupancy Calculator

- NVIDIA Nsight Compute User Manual: https://docs.nvidia.com/nsight-compute/nsight-compute-cli/

- cuBLAS Documentation: https://docs.nvidia.com/cuda/cublas/

**Research Papers**:
1. Dao et al., "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness", ICML 2022
   - Demonstrates low-occupancy, high-register design for attention

2. "CUTLASS: Fast Linear Algebra in CUDA C++" (NVIDIA)
   - Discusses kernel optimization strategies for matrix operations

**Practical Resources**:
- NVIDIA GTC Talks on kernel optimization
- CaffeConv blog: Efficient deep learning convolution kernels
- Mod.int blog: Register pressure and occupancy tradeoffs

---

**Document Version**: 1.0
**Last Updated**: 2026-07-07
**Sources**: NVIDIA official documentation, research papers, industrial practice (cuBLAS, TensorRT, FlashAttention)
