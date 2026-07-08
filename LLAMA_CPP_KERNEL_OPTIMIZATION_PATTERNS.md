# Llama.cpp Kernel Optimization Patterns: Technical Deep Dive

**Focus:** Concrete code patterns, optimization techniques, performance tuning strategies  
**Date:** July 7, 2026

---

## SECTION 1: CORE OPTIMIZATION PATTERNS IN MUL_MAT_Q

### 1.1 Bit Extraction Patterns

#### Pattern 1: Nibble Extraction (4-bit)

**Naive approach (inefficient):**
```cuda
uint8_t get_nibble(uint8_t byte, int position) {
    return (byte >> (position * 4)) & 0x0F;
}

// Used in loop:
for (int i = 0; i < 256; i++) {
    uint8_t byte_idx = i / 2;
    uint8_t nibble = get_nibble(y[byte_idx], i % 2);
}
```

**Optimized approach (vectorized):**
```cuda
// Load 4 bytes at once (8 nibbles)
uint32_t packed = *(uint32_t*)&y[k_base];

// Extract all 8 nibbles in parallel
uint8_t nibbles[8] = {
    (uint8_t)(packed & 0x0F),
    (uint8_t)((packed >> 4) & 0x0F),
    (uint8_t)((packed >> 8) & 0x0F),
    (uint8_t)((packed >> 12) & 0x0F),
    (uint8_t)((packed >> 16) & 0x0F),
    (uint8_t)((packed >> 20) & 0x0F),
    (uint8_t)((packed >> 24) & 0x0F),
    (uint8_t)((packed >> 28) & 0x0F),
};

// All 8 nibbles extracted with single load + 8 shifts/masks
// vs. 8 separate loads + shifts
```

**CUDA inline PTX (ultra-optimized):**
```cuda
// BFE (Bit Field Extract) instruction: single PTX op for any bit range
uint32_t bfe(uint32_t source, uint32_t start, uint32_t width) {
    uint32_t result;
    asm("bfe.u32 %0, %1, %2, %3;" : "=r"(result) : "r"(source), "r"(start), "r"(width));
    return result;
}

// Usage:
uint32_t packed = *(uint32_t*)&y[k_base];
uint8_t nibble0 = bfe(packed, 0, 4);   // bits [3:0]
uint8_t nibble1 = bfe(packed, 4, 4);   // bits [7:4]
uint8_t nibble2 = bfe(packed, 8, 4);   // bits [11:8]
// etc.

// Each BFE is single instruction (vs shift + mask = 2 instructions)
// Net savings: 8 instructions per 4 bytes unpacked
```

#### Pattern 2: Mixed 4-bit and 1-bit Extraction (5-bit reconstruction)

```cuda
// Q5_K_M structure:
// - 4-bit main value in qs[]
// - 1-bit high bit in qh[]

uint8_t extract_5bit(
    const uint8_t* qs,      // 4-bit values
    const uint8_t* qh,      // 1-bit high bits
    int idx,                // Index in block (0-255)
    uint8_t group_shift     // Shift within group
) {
    // Extract 4-bit value
    int byte_idx = (idx * 4) / 8;
    int bit_offset = (idx * 4) % 8;
    uint8_t val4 = (qs[byte_idx] >> bit_offset) & 0x0F;
    
    // Extract 1-bit value
    int qh_byte = (idx * 1) / 8;
    int qh_bit = (idx * 1) % 8;
    uint8_t val1 = (qh[qh_byte] >> qh_bit) & 0x01;
    
    // Combine: val1 is high bit, val4 is low 4 bits
    uint8_t val5 = (val1 << 4) | val4;
    
    return val5;
}

// Optimized version (fewer divisions/modulos):
#pragma unroll
for (int i = 0; i < 32; i++) {  // Process 32 values in group
    // Precompute byte and bit offsets
    int qs_byte = (i * 4) / 8;
    int qs_bit = (i * 4) % 8;
    
    int qh_byte = i / 8;
    int qh_bit = i % 8;
    
    uint8_t val4 = (qs[qs_byte] >> qs_bit) & 0x0F;
    uint8_t val1 = (qh[qh_byte] >> qh_bit) & 0x01;
    
    uint8_t val5 = (val1 << 4) | val4;
    // Use val5 in matmul
}
```

### 1.2 Dequantization Patterns

#### Pattern 1: Group Scale Caching

**Inefficient:** Reload scales for every weight in group
```cuda
for (int k = 0; k < K; k++) {
    int group_id = k / GROUP_SIZE;
    half scale = scales[group_id];  // Load from global memory every iteration
    half zero = zeros[group_id];
    
    uint8_t q = unpack_weight(y, k);
    float w = (float)scale * (float)q + (float)zero;
    acc += a[k] * w;
}
```

**Optimized:** Cache scales in registers
```cuda
// Preload scales for all 8 groups (Q4_K_M has 8 groups per 256-weight block)
half group_scales[8];
half group_zeros[8];

#pragma unroll 8
for (int g = 0; g < 8; g++) {
    group_scales[g] = scales[block_id * 8 + g];
    group_zeros[g] = zeros[block_id * 8 + g];
}

// Now loop only needs array indexing (fast), no global loads
for (int k = 0; k < K; k++) {
    int group_id = (k % 256) / GROUP_SIZE;  // 0-7
    half scale = group_scales[group_id];  // Register load (10+ cycle latency hidden)
    half zero = group_zeros[group_id];
    
    uint8_t q = unpack_weight(y, k);
    float w = (float)scale * (float)q + (float)zero;
    acc += a[k] * w;
}
```

#### Pattern 2: Convert Quantized Integer to Signed

**For 4-bit (Q4):** Quantized values are unsigned [0, 15], but represent signed range [-8, 7]

```cuda
// Naive:
int8_t signed_val = (int8_t)quant_val - 8;  // Subtract offset

// Better: Reinterpret as signed (negative values already in two's complement)
// In 4-bit: 0=0, 1=1, ..., 7=7, 8=-8, 9=-7, ..., 15=-1
int8_t convert_q4_to_signed(uint8_t q) {
    // Shift left by 4 bits, then arithmetic right shift
    // This handles sign extension automatically
    return (int8_t)(q << 4) >> 4;
}

// Usage:
uint8_t q = 0x0F;  // 15 in unsigned
int8_t s = convert_q4_to_signed(q);  // -1

// Verify:
// q = 15 (binary: 1111)
// shift left: 11110000 (as signed char: -16)
// arithmetic right 4: 11111111 (-1 in two's complement)
```

**For 2-bit (Q2):** Values [0, 3] represent signed [-2, 1]

```cuda
int8_t convert_q2_to_signed(uint8_t q) {
    // Same pattern: shift left by 6, arithmetic right 6
    return (int8_t)(q << 6) >> 6;
}

// Verify: q=3 (binary: 11) → 11000000 (-64) → 11111111 (-1) [actually verify this]
```

#### Pattern 3: FMA (Fused Multiply-Add) for Dequantization

```cuda
// Standard: Separate multiply and add
float w_quant = (float)q;
float w_scaled = w_quant * scale;
float w_dequant = w_scaled + zero;

// FMA: Combined multiply-add in single instruction
float w_dequant = __fma_rn((float)q, scale, zero);

// Benefits:
// 1. Single instruction instead of two
// 2. Full precision throughout (no intermediate rounding)
// 3. Faster on GPUs with native FMA support (all modern NVIDIA)

// Alternative with higher precision:
float w_dequant = fma((float)q, scale, zero);  // Default rounding mode
float w_dequant_rn = __fma_rn((float)q, scale, zero);  // Round-to-nearest
float w_dequant_rd = __fma_rd((float)q, scale, zero);  // Round-down
```

### 1.3 Accumulation Patterns

#### Pattern 1: Per-Thread vs Warp-Level Accumulation

**Per-thread (simple but slower):**
```cuda
float local_acc = 0.0f;
for (int k = 0; k < K; k++) {
    float a = (float)x[row * K + k];
    float w = dequant_weight(y, k);
    local_acc += a * w;
}
// Now need to reduce across warp
```

**Warp-level reduction (optimized):**
```cuda
// Each thread in warp accumulates for different output columns
float acc[ACCUM_N];  // Register array, typically 4-8 elements
#pragma unroll
for (int i = 0; i < ACCUM_N; i++) {
    acc[i] = 0.0f;
}

for (int k = 0; k < K; k++) {
    float a = (float)x[row * K + k];
    
    #pragma unroll
    for (int n = 0; n < ACCUM_N; n++) {
        float w = dequant_weight(y, k * ACCUM_N + (lane_id * ACCUM_N + n));
        acc[n] += a * w;
    }
}

// Store results
#pragma unroll
for (int n = 0; n < ACCUM_N; n++) {
    int col = lane_id * ACCUM_N + n;
    if (col < N) {
        dst[row * N + col] = acc[n];
    }
}
```

#### Pattern 2: Double Accumulation (FP32 vs FP16)

```cuda
// FP16 accumulation (fast but lossy):
half acc_half = 0.0f;
for (int k = 0; k < K; k++) {
    half a = x[k];
    half w = dequant_to_half(y, k);
    acc_half += a * w;  // FP16 addition: 4-bit precision loss
}
// ❌ Not recommended: accumulation errors cascade

// FP32 accumulation (correct):
float acc_float = 0.0f;
for (int k = 0; k < K; k++) {
    float a = __half2float(x[k]);
    float w = __half2float(dequant_to_half(y, k));
    acc_float += a * w;  // Full precision, ~2% overhead
}
half result = __float2half(acc_float);

// Mixed approach (recommended):
// Load activations as FP16, convert on-the-fly
float acc = 0.0f;
half a_half;
for (int k = 0; k < K; k++) {
    a_half = x[k];  // Load FP16
    float a = __half2float(a_half);  // Convert to FP32
    float w = dequant_weight_fp32(y, k);
    acc += a * w;  // Accumulate in FP32
}
```

---

## SECTION 2: MEMORY OPTIMIZATION PATTERNS

### 2.1 Shared Memory Layout

#### Pattern 1: Avoid Bank Conflicts

**Bank conflict setup (bad):**
```cuda
// NVIDIA GPUs have 32 banks, each bank is 4 bytes
// If two threads access same bank → conflict

__shared__ uint8_t weights[256];  // No padding
// Thread 0 accesses weights[0]   (bank 0)
// Thread 32 accesses weights[32] (bank 0)  <- Conflict!
// Results in 2 separate transactions instead of 1
```

**Conflict-free layout (good):**
```cuda
// Add padding: 1 byte per bank (32 bytes total)
__shared__ uint8_t weights[256 + 32];  // Padding

// Or for FP32:
__shared__ float weights[256 + 8];  // 1 float (4 bytes) = 1 bank shift

// Now:
// Thread 0 accesses weights[0]   (bank 0)
// Thread 32 accesses weights[32+8] (bank 8) <- No conflict!
```

#### Pattern 2: Shared Memory Tiling

```cuda
// Load activation tile into shared memory
__shared__ half shared_x[TILE_K];

// All threads cooperatively load TILE_K elements
for (int i = threadIdx.x; i < TILE_K; i += blockDim.x) {
    int k = k_base + i;
    if (k < K) {
        shared_x[i] = x[row * K + k];  // Coalesced reads
    }
}
__syncthreads();

// Compute phase: all threads read from shared (fast)
for (int k = 0; k < TILE_K; k++) {
    float a = (float)shared_x[k];
    float w = dequant_weight(y, k_base + k);
    acc += a * w;
}
```

### 2.2 Global Memory Coalescing

#### Pattern 1: Coalesced Reads of Packed Data

```cuda
// BAD: Each thread reads separate byte
for (int k = threadIdx.x; k < K; k += blockDim.x) {
    uint8_t packed = y[k / 2];  // Non-contiguous across threads
    // Thread 0 → y[0]
    // Thread 1 → y[0]  (same address!)
    // Thread 2 → y[1]
    // Thread 3 → y[1]  (same address!)
    // Only 2 transactions needed but GPU sees random access
}

// GOOD: Vectorized load
int block_start = blockIdx.x * blockDim.x * ELEMENTS_PER_THREAD;
for (int t = threadIdx.x; t < THREAD_LOAD_SIZE; t += blockDim.x) {
    int idx = block_start + t;
    
    // Load 16 bytes at once (4 uint32)
    uint4 packed = *(uint4*)&y[idx];
    
    // All 32 threads load contiguous 512 bytes in single transaction
    // (32 threads × 16 bytes = 512 bytes)
}
```

#### Pattern 2: Read-Once-Use-Many (ROUM)

```cuda
// Pattern: Load quantized data, unpack, use immediately
// Never reload same data

__global__ void mul_mat_q_roum(const uint8_t* y, const half* x, float* dst, ...) {
    float acc = 0.0f;
    
    for (int k = 0; k < K; k++) {
        // Phase 1: Load packed data (1 byte from global)
        int packed_idx = k / 2;
        uint8_t packed = y[packed_idx];
        
        // Phase 2: Unpack immediately (register operations)
        uint8_t q = (k % 2) ? (packed >> 4) : (packed & 0x0F);
        
        // Phase 3: Dequantize and use (register operations)
        int group_id = k / GROUP_SIZE;
        float scale = scales[group_id];
        float w = (float)(int8_t)(q - 8) * scale;
        
        // Phase 4: Multiply-accumulate (computation)
        float a = (float)x[k];
        acc += a * w;
    }
    
    dst[row] = acc;
}
// Each byte loaded ONCE, immediately consumed
// Minimal register pressure (only current values)
```

---

## SECTION 3: INSTRUCTION-LEVEL OPTIMIZATION

### 3.1 Loop Unrolling Patterns

#### Pattern 1: Manual Unrolling for Latency Hiding

```cuda
// No unroll: 4-cycle FMA latency blocks each iteration
float acc = 0.0f;
for (int i = 0; i < 100; i++) {
    float a = load_a(i);
    float b = load_b(i);
    acc += a * b;  // Wait for previous add to finish (4-cycle latency)
}

// 4-way unroll: 4 independent accumulators hide latency
float acc0 = 0.0f, acc1 = 0.0f, acc2 = 0.0f, acc3 = 0.0f;
for (int i = 0; i < 100; i += 4) {
    float a0 = load_a(i+0), a1 = load_a(i+1), a2 = load_a(i+2), a3 = load_a(i+3);
    float b0 = load_b(i+0), b1 = load_b(i+1), b2 = load_b(i+2), b3 = load_b(i+3);
    
    acc0 += a0 * b0;  // Execute FMA0
    acc1 += a1 * b1;  // Execute FMA1 (while FMA0 computing)
    acc2 += a2 * b2;  // Execute FMA2 (while FMA1 computing)
    acc3 += a3 * b3;  // Execute FMA3 (while FMA2 computing)
    // By time loop iteration ends, FMA0 results ready
}
float final_acc = acc0 + acc1 + acc2 + acc3;

// Benefit: 3-4x throughput improvement through instruction parallelism
```

#### Pattern 2: Pragma Unroll (Compiler-Directed)

```cuda
// Compiler decides unroll factor
for (int k = 0; k < K; k++) {
    acc += process_element(k);
}

// Explicit unroll factor
#pragma unroll 8
for (int k = 0; k < K; k++) {
    acc += process_element(k);
}

// No unroll (sometimes needed for register pressure)
#pragma unroll 1
for (int k = 0; k < K; k++) {
    acc += process_element(k);
}

// Dynamic unroll based on compile-time constant
#pragma unroll UNROLL_FACTOR
for (int k = 0; k < K; k++) {
    acc += process_element(k);
}
```

### 3.2 Instruction Scheduling

#### Pattern 1: Instruction Interleaving

```cuda
// Poor scheduling (dependencies):
float x = load_from_memory();  // 400-cycle latency
float y = x * 2.0f;            // Stalls, waiting for x
float z = y + 1.0f;            // Stalls, waiting for y
store(z);                      // Execute 400+ cycles later

// Good scheduling (independent operations):
float x = load_from_memory();     // Issue, will stall
float b = load_from_memory2();    // Issue while x is loading
float y = x * 2.0f;               // Stalls (dependency on x)
float c = b + 1.0f;               // Execute (no dependency on x)
float z = y * c;                  // Execute (independent)
store(z);

// GPU can hide memory latency by executing other instructions
```

#### Pattern 2: Reduce Dependency Chains

```cuda
// Chain dependency (bad):
float acc = 0.0f;
for (int i = 0; i < 1000; i++) {
    acc = acc + get_value(i);  // Each iteration depends on previous
    // Can only achieve 1 addition per 4 cycles (FMA latency)
}

// Independent accumulators (good):
float acc[4] = {0};
for (int i = 0; i < 1000; i += 4) {
    acc[0] += get_value(i+0);
    acc[1] += get_value(i+1);
    acc[2] += get_value(i+2);
    acc[3] += get_value(i+3);
}
// 4 independent FMAs execute in parallel
float result = acc[0] + acc[1] + acc[2] + acc[3];
```

---

## SECTION 4: WARP-LEVEL OPTIMIZATION

### 4.1 Warp Shuffle Patterns

#### Pattern 1: Warp Reduction

```cuda
// Reduce 32 values in warp to single value
float reduce_warp(float val) {
    // Round 1: [32] → [16]
    val += __shfl_down_sync(0xffffffff, val, 16);
    
    // Round 2: [16] → [8]
    val += __shfl_down_sync(0xffffffff, val, 8);
    
    // Round 3: [8] → [4]
    val += __shfl_down_sync(0xffffffff, val, 4);
    
    // Round 4: [4] → [2]
    val += __shfl_down_sync(0xffffffff, val, 2);
    
    // Round 5: [2] → [1]
    val += __shfl_down_sync(0xffffffff, val, 1);
    
    // Now in lane 0
    return val;
}

// Usage in kernel:
float acc = compute_partial_sum();
float result = reduce_warp(acc);
if (threadIdx.x % 32 == 0) {
    output[result_id] = result;  // Only lane 0 writes
}
```

#### Pattern 2: Warp Broadcast

```cuda
// Broadcast value from lane 0 to all lanes
float broadcast_warp(float val) {
    return __shfl_sync(0xffffffff, val, 0);  // Lane 0's value to all
}

// Usage: Broadcast scale factor to all lanes
if (threadIdx.x % 32 == 0) {
    float scale = scales[group_id];
    lane0_scale = scale;
}
scale = broadcast_warp(lane0_scale);
// Now all 32 lanes have same scale value
```

### 4.2 Divergence-Free Patterns

#### Pattern 1: Eliminate Conditional in Hot Loop

```cuda
// BAD: Divergence in inner loop
float acc = 0.0f;
for (int k = 0; k < K; k++) {
    uint8_t q = unpack_weight(y, k);
    if (k % 256 < 128) {  // Divergence: half warps take different paths
        acc += a * (float)q * scale0;
    } else {
        acc += a * (float)q * scale1;
    }
}

// GOOD: Predicated execution (no divergence)
float acc = 0.0f;
for (int k = 0; k < K; k++) {
    uint8_t q = unpack_weight(y, k);
    
    // Both paths execute (but only one writes)
    float result0 = a * (float)q * scale0;
    float result1 = a * (float)q * scale1;
    
    // Conditional select (no branching)
    float result = (k % 256 < 128) ? result0 : result1;
    acc += result;
}

// BETTER: Pre-load scale factors
half scale = (k % 256 < 128) ? scale0 : scale1;
// Outside loop:
for (int k = 0; k < K; k++) {
    uint8_t q = unpack_weight(y, k);
    acc += a * (float)q * scale;
}
```

---

## SECTION 5: REGISTER PRESSURE MANAGEMENT

### 5.1 Detecting Register Pressure

**Use NVIDIA SASS Analysis:**
```bash
# Compile with register info
nvcc -ptx -o kernel.ptx kernel.cu
# Extract register usage from .reg directives

# Or use cuobjdump
cuobjdump --dump-sass a.out | grep -E "REG|LAUNCH"
```

**Profiler metrics:**
```bash
# Using ncu:
ncu --set full --export data.ncu-rep ./program

# Key metrics:
# - "Per Warp Registers": Average registers per thread
# - "SM Occupancy": Threads per SM (limited by register file size)
```

### 5.2 Register Pressure vs Occupancy Trade-off

**Case 1: High Occupancy, Low Register Pressure**
```cuda
#define REGISTERS_PER_THREAD 15  // Low pressure
#define BLOCK_SIZE 256          // 8 threads per SM × 1024 regs = 8192 regs available

// 1024 registers / 15 registers = 68 threads per SM
// 68 threads / 32 (warp size) = 2.1 warps per SM

// Occupancy: 2 warps × 32 threads = 64 threads per SM
// Utilization: 64 / 1024 = 6.25% (LOW)

// Benefit: Better latency hiding through context switching
// Drawback: Few independent operations, poor throughput
```

**Case 2: Low Occupancy, High Register Reuse**
```cuda
#define REGISTERS_PER_THREAD 60  // High pressure
#define BLOCK_SIZE 256          // 8 threads per SM × 1024 regs = 8192 regs available

// 1024 registers / 60 registers = 17 threads per SM
// 17 threads / 32 (warp size) = 0.5 warps per SM (rounded up to 1 warp)

// Occupancy: 1 warp × 32 threads = 32 threads per SM
// Utilization: 32 / 1024 = 3.1% (VERY LOW)

// Benefit: High register reuse, better computation efficiency
// Drawback: Low latency hiding, stalls on memory loads

// Verdict: Only worth if compute is heavy enough to hide loads
// For memory-bound workloads (like mul_mat_q), avoid
```

**Optimal for mul_mat_q: 20-40 registers per thread**
- Allows 2-3 warps per SM
- Enough registers for local accumulation and unpacking buffers
- Good latency hiding for memory-bound operations

### 5.3 Techniques to Reduce Register Pressure

#### Technique 1: Use Shared Memory Instead of Registers

```cuda
// BAD: Large local array uses registers
float local_buffer[256];
#pragma unroll
for (int i = 0; i < 256; i++) {
    local_buffer[i] = process(i);  // 256 × 4 bytes = 1024 bytes = 256 registers!
}

// GOOD: Use shared memory
__shared__ float shared_buffer[BLOCK_SIZE][256/BLOCK_SIZE];
int tid = threadIdx.x;
for (int i = tid; i < 256; i += blockDim.x) {
    shared_buffer[tid][i / BLOCK_SIZE] = process(i);  // Only O(1) registers
}
__syncthreads();
```

#### Technique 2: Reduce Unroll Factor

```cuda
// High register pressure:
#pragma unroll 8
for (int k = 0; k < K; k++) {
    // 8 iterations worth of temp variables in registers
}

// Lower register pressure:
#pragma unroll 4
for (int k = 0; k < K; k++) {
    // 4 iterations worth of temp variables
}

// No unroll (if register pressure critical):
#pragma unroll 1
for (int k = 0; k < K; k++) {
    // Minimal temp variables per iteration
}
```

#### Technique 3: Use Half-Precision

```cuda
// FP32 everything (high register pressure):
float a = (float)x[k];
float w = dequant_fp32(y, k);
float product = a * w;
acc += product;

// Mixed precision (reduced register pressure):
half a_half = x[k];              // 2 bytes per element
float a = __half2float(a_half);  // Conversion implicit, not extra register
half w_half = dequant_to_half(y, k);
float w = __half2float(w_half);
float product = a * w;
acc += product;

// Result: Fewer registers for input storage, same computation precision
```

---

## SECTION 6: PERFORMANCE VALIDATION PATTERNS

### 6.1 Micro-Benchmarking

```cuda
__global__ void benchmark_kernel(
    const half* x,
    const uint8_t* y,
    float* dst,
    int K, int N
) {
    // Block-level timing
    __shared__ uint64_t start_time;
    __shared__ uint64_t end_time;
    
    if (threadIdx.x == 0) {
        start_time = clock64();
    }
    __syncthreads();
    
    // Actual kernel work
    float acc = 0.0f;
    for (int k = 0; k < K; k++) {
        half a = x[k];
        uint8_t q = y[k];
        acc += (float)a * (float)q;
    }
    
    __syncthreads();
    if (threadIdx.x == 0) {
        end_time = clock64();
    }
    __syncthreads();
    
    // Compute cycles per work item
    uint64_t cycles = end_time - start_time;
    uint64_t work_items = blockDim.x * K;
    dst[blockIdx.x] = (float)cycles / (float)work_items;
}
```

### 6.2 Roofline Model Analysis

```python
# Roofline performance model
# Performance (GFLOPS) vs Arithmetic Intensity (FLOP/Byte)

peak_compute = 100.0  # GFLOPS (RTX 4090)
peak_bandwidth = 1152.0  # GB/s (RTX 4090)

# Convert bandwidth to GFLOPS/GB = 8 FLOPS/Byte × 1000 = 8000 GFLOPS @ 1 GB/s
memory_roof = peak_bandwidth * 1  # Assuming 1 FLOP per byte on average

# For mul_mat_q4_k:
# Input: M × K × 2 bytes (FP16)
# Quantized weights: K × N / 2 bytes (4-bit)
# Output: M × N × 4 bytes
# Compute: 2 × M × K × N FLOPs

total_bytes = (M * K * 2) + (K * N / 2) + (M * N * 4)
total_flops = 2 * M * K * N
arithmetic_intensity = total_flops / total_bytes

performance_limited_by = min(peak_compute, memory_roof * arithmetic_intensity)

# For M=1, K=4096, N=11008:
# total_bytes ≈ 22.7 MB
# total_flops ≈ 90 M
# AI ≈ 4 FLOP/Byte
# Performance = min(100, 8000 × 4) = min(100, 32000) = 100 GFLOPS
# (Compute-bound? No, bandwidth-bound, only ~3% of peak)
```

---

## SECTION 7: COMMON PITFALLS AND DEBUGGING

### 7.1 Common Mistakes

**Mistake 1: Incorrect Bit Extraction Order**
```cuda
// For Q5_K_M: 4-bit value in qs[], 1-bit in qh[]
// WRONG: Takes bits [1:0] instead of [4:1]
uint8_t val4 = (qs[idx] >> (bit_offset % 8)) & 0x0F;  // Off-by-one error

// CORRECT:
int byte_idx = (idx * 4) / 8;
int bit_in_byte = (idx * 4) % 8;
uint8_t val4 = (qs[byte_idx] >> bit_in_byte) & 0x0F;
```

**Mistake 2: Missing Synchronization in Shared Memory Access**
```cuda
// WRONG: Race condition
__shared__ float shared[256];
shared[threadIdx.x] = data[threadIdx.x];
float result = shared[threadIdx.x + 1];  // Other thread might not have written yet

// CORRECT:
__shared__ float shared[256];
shared[threadIdx.x] = data[threadIdx.x];
__syncthreads();  // Ensure all writes complete
float result = shared[threadIdx.x + 1];  // Now safe
```

**Mistake 3: Bank Conflicts in Shared Memory**
```cuda
// WRONG: Threads 0, 32 access same bank
__shared__ uint8_t data[256];
uint8_t val0 = data[threadIdx.x];
uint8_t val1 = data[threadIdx.x + 32];  // Bank conflict

// CORRECT: Add padding
__shared__ uint8_t data[256 + 32];  // 32-byte padding
uint8_t val0 = data[threadIdx.x];
uint8_t val1 = data[threadIdx.x + 32];  // Different banks
```

**Mistake 4: Register Overflow (Implicit Spilling)**
```cuda
// WRONG: Compiler spills to local memory (slow!)
float accum[1024];  // Way too much for registers

// CORRECT: Stay within register budget
float accum[4];  // 16 registers
__shared__ float shared_accum[256];  // Use shared for larger buffers
```

### 7.2 Debugging Strategies

**Strategy 1: Print Intermediate Values**
```cuda
// Debug kernel
__global__ void debug_kernel(...) {
    int thread_id = blockIdx.x * blockDim.x + threadIdx.x;
    
    if (thread_id == 0) {  // Only print for one thread to avoid spam
        uint8_t q = unpack_weight(y, 0);
        float scale = scales[0];
        float w = (float)q * scale;
        printf("q[0] = %d, scale[0] = %f, w = %f\n", q, scale, w);
    }
}

// Run and check output
./program > output.txt
grep "q\[0\]" output.txt
```

**Strategy 2: Compare with CPU Reference**
```python
import numpy as np

# CPU implementation
def mul_mat_q_cpu(x, y_quant, scales, zeros):
    M, K = x.shape
    N = y_quant.shape[1]
    result = np.zeros((M, N), dtype=np.float32)
    
    for m in range(M):
        for n in range(N):
            for k in range(K):
                q = (y_quant[k // 2, n] >> ((k % 2) * 4)) & 0x0F
                group_id = k // 32
                scale = scales[group_id]
                w = (q - 8) * scale
                result[m, n] += x[m, k] * w
    
    return result

# Run GPU kernel
result_gpu = cuda_mul_mat_q(x, y_quant, scales, zeros)

# Compare
max_error = np.abs(result_cpu - result_gpu).max()
print(f"Max error: {max_error}")
if max_error > 1e-3:
    # Find first mismatched element
    idx = np.unravel_index(np.argmax(np.abs(result_cpu - result_gpu)), result_cpu.shape)
    print(f"Mismatch at {idx}: CPU={result_cpu[idx]}, GPU={result_gpu[idx]}")
```

**Strategy 3: Profile with NVIDIA Tools**
```bash
# Basic profiling
nsys profile -t cuda,cublas -o profile.nsys-rep ./program

# Detailed metrics
ncu --set full -o metrics.ncu-rep ./program
ncu --import metrics.ncu-rep

# Check key metrics:
# - Global Load Transactions (should be low, coalesced)
# - Shared Memory Bank Conflicts (should be 0%)
# - Register Pressure (should be 20-40 registers/thread)
# - SM Occupancy (should be 50-75%)
```

---

## CONCLUSIONS

The most impactful optimization patterns for mul_mat_q kernels are:

1. **Vectorized bit unpacking** (5-10% improvement)
2. **Caching scales in registers** (8-15% improvement)
3. **Register blocking for accumulation** (10-20% improvement)
4. **Double buffering for load/compute overlap** (10-15% improvement)
5. **Warp-level synchronization** (5-10% improvement)

Combined, these can provide 40-70% speedup over naive implementations, though even optimized kernels remain memory-bandwidth limited (~11-15% of peak GPU throughput).

